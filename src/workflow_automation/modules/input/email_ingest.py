from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
    RunMode,
)


MODULE_ID = "email.ingest"
DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "email"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
MAX_RESULTS = 50
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_QUERY_CHARS = 500


class EmailIngest:
    """Read bounded message metadata and attachments from fixtures or Gmail REST.

    Gmail calls are GET-only and require `GOOGLE_GMAIL_ACCESS_TOKEN`, provisioned
    with Gmail read access. The module never marks messages read or changes labels.
    It makes one list request, does not paginate, and fetches messages sequentially.
    """

    module_id = MODULE_ID

    def __init__(
        self,
        *,
        fixture_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        api_base_url: str = GMAIL_API,
        timeout_seconds: float = 15.0,
        max_attachment_bytes: int = MAX_ATTACHMENT_BYTES,
        max_total_attachment_bytes: int = MAX_TOTAL_ATTACHMENT_BYTES,
    ) -> None:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be greater than 0 and at most 60")
        if not 0 < max_attachment_bytes <= MAX_ATTACHMENT_BYTES:
            raise ValueError(f"max_attachment_bytes must be from 1 to {MAX_ATTACHMENT_BYTES}")
        if not 0 < max_total_attachment_bytes <= MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError(f"max_total_attachment_bytes must be from 1 to {MAX_TOTAL_ATTACHMENT_BYTES}")
        self.fixture_dir = fixture_dir or DEFAULT_FIXTURE_DIR
        self.transport = transport
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        try:
            source = payload.get("source")
            if source == "fixture":
                fixture_id = payload.get("fixture_id")
                items = self._fixture_items(fixture_id)
                for item in items:
                    item["source_system"] = "gmail_fixture"
                    item["source_account"] = f"fixture:{fixture_id}"
                return self._result(context, ResultState.SUCCESS, {"items": items},
                                    metrics={"messages_read": len(items)})
            if source != "gmail":
                return self._failure(context, "INVALID_SOURCE", "VALIDATION",
                                     "source must be 'fixture' or 'gmail'",
                                     Retryability.NOT_RETRYABLE)
            if context.mode in (RunMode.MOCK, RunMode.REPLAY):
                return self._failure(context, "LIVE_PROVIDER_DISABLED", "MODE",
                                     "Gmail access is disabled in MOCK and REPLAY modes",
                                     Retryability.NOT_RETRYABLE)
            return self._gmail_items(payload, context)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return self._failure(context, "INVALID_INPUT_OR_FIXTURE", "VALIDATION",
                                 str(exc), Retryability.NOT_RETRYABLE)

    def _fixture_items(self, fixture_id: Any) -> list[dict[str, Any]]:
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError("fixture_id is required when source is 'fixture'")
        path = self.fixture_dir / "emails.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        fixtures = data.get("fixtures")
        if not isinstance(fixtures, dict) or fixture_id not in fixtures:
            raise ValueError(f"Unknown email fixture_id: {fixture_id}")
        items = fixtures[fixture_id]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError(f"Email fixture {fixture_id!r} must contain a list of message objects")
        return items

    def _gmail_items(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        query = payload.get("query", "")
        max_results = payload.get("max_results", 10)
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARS:
            return self._failure(context, "INVALID_QUERY", "VALIDATION",
                                 f"query must be a string of at most {MAX_QUERY_CHARS} characters",
                                 Retryability.NOT_RETRYABLE)
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= MAX_RESULTS:
            return self._failure(context, "INVALID_MAX_RESULTS", "VALIDATION",
                                 f"max_results must be an integer from 1 to {MAX_RESULTS}",
                                 Retryability.NOT_RETRYABLE)
        source_account = payload.get("source_account")
        if (not isinstance(source_account, str) or not source_account.strip()
                or len(source_account) > 200 or "@" in source_account):
            return self._failure(context, "SOURCE_ACCOUNT_REQUIRED", "VALIDATION",
                                 "source_account must be a stable, non-sensitive key (not an email address) of at most 200 characters",
                                 Retryability.NOT_RETRYABLE)
        source_account = source_account.strip()
        token = os.environ.get("GOOGLE_GMAIL_ACCESS_TOKEN")
        if not token:
            return self._failure(context, "MISSING_GMAIL_TOKEN", "AUTHENTICATION",
                                 "GOOGLE_GMAIL_ACCESS_TOKEN is not configured",
                                 Retryability.NOT_RETRYABLE)

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                response = _bounded_get(
                    client,
                    f"{self.api_base_url}/users/me/messages",
                    params={"q": query, "maxResults": max_results},
                    headers=headers,
                    max_bytes=1024 * 1024,
                )
                if response.is_error:
                    return self._http_failure(context, response.status_code)
                listing = response.json()
                if not isinstance(listing, dict):
                    raise ValueError("Gmail message list response has an invalid shape")
                refs = listing.get("messages", [])
                if not isinstance(refs, list):
                    raise ValueError("Gmail message list response has an invalid shape")
                if len(refs) > max_results:
                    refs = refs[:max_results]
                items: list[dict[str, Any]] = []
                skipped_attachments = 0
                message_errors = 0
                attachment_bytes = 0
                for ref in refs:
                    message_id = ref.get("id") if isinstance(ref, dict) else None
                    if not isinstance(message_id, str) or not message_id:
                        message_errors += 1
                        continue
                    try:
                        message_response = _bounded_get(
                            client,
                        f"{self.api_base_url}/users/me/messages/{quote(message_id, safe='')}",
                        params={"format": "full"},
                        headers=headers,
                            max_bytes=((self.max_total_attachment_bytes + 2) // 3) * 4 + 1024 * 1024,
                        )
                    except _ResponseTooLarge:
                        message_errors += 1
                        continue
                    if message_response.is_error:
                        if message_response.status_code in (401, 403, 429) or message_response.status_code >= 500:
                            return self._http_failure(context, message_response.status_code)
                        message_errors += 1
                        continue
                    message = message_response.json()
                    try:
                        item, skipped, received_bytes = self._map_gmail_message(
                            client, message, headers,
                            source_account=source_account,
                            max_total_bytes=self.max_total_attachment_bytes - attachment_bytes,
                        )
                    except _AttachmentHttpError as exc:
                        return self._http_failure(context, exc.status_code)
                    except ValueError:
                        message_errors += 1
                        continue
                    items.append(item)
                    skipped_attachments += skipped
                    attachment_bytes += received_bytes
        except httpx.TimeoutException:
            return self._failure(context, "GMAIL_TIMEOUT", "TRANSIENT",
                                 "Gmail request timed out", Retryability.RETRYABLE)
        except httpx.TransportError:
            return self._failure(context, "GMAIL_TRANSPORT_ERROR", "TRANSIENT",
                                 "Gmail transport failed", Retryability.RETRYABLE)
        except (ValueError, json.JSONDecodeError):
            return self._failure(context, "INVALID_GMAIL_RESPONSE", "PROVIDER_RESPONSE",
                                 "Gmail returned an invalid response", Retryability.UNKNOWN)

        warnings: list[str] = []
        if listing.get("nextPageToken"):
            warnings.append("Additional matching messages were not fetched; this module reads one bounded page.")
        if skipped_attachments:
            warnings.append(f"{skipped_attachments} attachment(s) exceeded the configured size limit and were omitted.")
        if message_errors:
            warnings.append(f"{message_errors} message(s) could not be mapped from the provider response.")
        status = ResultState.PARTIAL if (warnings or message_errors) else ResultState.SUCCESS
        return self._result(context, status, {"items": items}, warnings=warnings,
                            metrics={"messages_read": len(items),
                                     "attachments_omitted": skipped_attachments,
                                     "messages_failed": message_errors})

    def _map_gmail_message(
        self,
        client: httpx.Client,
        message: dict[str, Any],
        headers: dict[str, str],
        *,
        source_account: str,
        max_total_bytes: int,
    ) -> tuple[dict[str, Any], int, int]:
        if not isinstance(message, dict) or not isinstance(message.get("id"), str):
            raise ValueError("Gmail message response has no provider ID")
        payload = message.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("Gmail message payload has an invalid shape")
        message_headers = payload.get("headers", [])
        if not isinstance(message_headers, list):
            raise ValueError("Gmail message headers have an invalid shape")
        headers_by_name = {
            str(header.get("name", "")).casefold(): str(header.get("value", ""))
            for header in message_headers if isinstance(header, dict)
        }
        attachments: list[dict[str, Any]] = []
        omitted = 0
        total_bytes = 0
        for part in self._walk_parts(payload):
            filename = part.get("filename")
            body = part.get("body") or {}
            if not isinstance(body, dict):
                continue
            provider_attachment_id = body.get("attachmentId")
            if not filename or (not provider_attachment_id and not body.get("data")):
                continue
            declared_size = body.get("size", 0)
            remaining_bytes = min(self.max_attachment_bytes, max_total_bytes - total_bytes)
            if remaining_bytes <= 0 or (isinstance(declared_size, int) and declared_size > remaining_bytes):
                omitted += 1
                continue
            if provider_attachment_id:
                try:
                    response = _bounded_get(
                        client,
                    f"{self.api_base_url}/users/me/messages/{quote(message['id'], safe='')}/attachments/{quote(str(provider_attachment_id), safe='')}",
                        headers=headers,
                        max_bytes=((remaining_bytes + 2) // 3) * 4 + 1024,
                    )
                except _ResponseTooLarge:
                    omitted += 1
                    continue
                if response.is_error:
                    raise _AttachmentHttpError(response.status_code)
                attachment_response = response.json()
                if not isinstance(attachment_response, dict):
                    raise ValueError("Gmail attachment response has an invalid shape")
                data = attachment_response.get("data", "")
                attachment_id = str(provider_attachment_id)
            else:
                data = body["data"]
                attachment_id = f"inline:{part.get('partId', 'unknown')}"
            if not isinstance(data, str):
                raise ValueError("Gmail attachment response has an invalid shape")
            if len(data) > ((remaining_bytes + 2) // 3) * 4 + 4:
                omitted += 1
                continue
            raw = _decode_base64url(data)
            if len(raw) > remaining_bytes:
                omitted += 1
                continue
            attachments.append({
                "attachment_id": attachment_id,
                "filename": str(filename),
                "mime_type": str(part.get("mimeType") or "application/octet-stream"),
                "content_base64": base64.b64encode(raw).decode("ascii"),
            })
            total_bytes += len(raw)
        return ({
            "message_id": message["id"],
            "thread_id": str(message.get("threadId", "")),
            "source_system": "gmail",
            "source_account": source_account,
            "subject": headers_by_name.get("subject", ""),
            "sender": headers_by_name.get("from", ""),
            "attachments": attachments,
        }, omitted, total_bytes)

    @staticmethod
    def _walk_parts(node: dict[str, Any]):
        if not isinstance(node, dict):
            return
        yield node
        parts = node.get("parts", [])
        if isinstance(parts, list):
            for part in parts:
                yield from EmailIngest._walk_parts(part)

    def _http_failure(self, context: ExecutionContext, status_code: int) -> ModuleResult:
        if status_code in (401, 403):
            return self._failure(context, "GMAIL_AUTHORIZATION_FAILED", "AUTHENTICATION",
                                 "Gmail rejected the configured access token or required permissions",
                                 Retryability.NOT_RETRYABLE, details={"http_status": status_code})
        if status_code == 429:
            return self._failure(context, "GMAIL_RATE_LIMITED", "RATE_LIMIT",
                                 "Gmail rate limit was reached", Retryability.RETRYABLE,
                                 details={"http_status": status_code})
        if status_code >= 500:
            return self._failure(context, "GMAIL_PROVIDER_UNAVAILABLE", "TRANSIENT",
                                 "Gmail provider returned a server error", Retryability.RETRYABLE,
                                 details={"http_status": status_code})
        return self._failure(context, "GMAIL_REQUEST_REJECTED", "PROVIDER_RESPONSE",
                             "Gmail rejected the request", Retryability.NOT_RETRYABLE,
                             details={"http_status": status_code})

    @staticmethod
    def _result(
        context: ExecutionContext,
        status: ResultState,
        business_result: dict[str, Any],
        *,
        warnings: list[str] | None = None,
        metrics: dict[str, int | float | str | bool] | None = None,
    ) -> ModuleResult:
        return ModuleResult(module_id=MODULE_ID, status=status, business_result=business_result,
                            warnings=warnings or [], metrics=metrics or {}, context=context).complete()

    @staticmethod
    def _failure(
        context: ExecutionContext,
        code: str,
        category: str,
        message: str,
        retryability: Retryability,
        *,
        details: dict[str, Any] | None = None,
    ) -> ModuleResult:
        return ModuleResult(
            module_id=MODULE_ID,
            status=ResultState.FAILURE,
            errors=[ModuleError(code=code, category=category, message=message,
                                retryability=retryability, details=details or {})],
            context=context,
        ).complete()


class _AttachmentHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _ResponseTooLarge(Exception):
    pass


def _bounded_get(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    max_bytes: int,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """Perform a GET while limiting bytes read into process memory."""
    with client.stream("GET", url, headers=headers, params=params) as response:
        if response.is_error:
            return httpx.Response(response.status_code, headers=response.headers,
                                  request=response.request)
        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
            raise _ResponseTooLarge
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise _ResponseTooLarge
        return httpx.Response(response.status_code, headers=response.headers,
                              content=bytes(body), request=response.request)


def _decode_base64url(data: Any) -> bytes:
    if not isinstance(data, str):
        raise ValueError("Gmail attachment data is not a string")
    normalized = data.replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    try:
        return base64.b64decode(normalized, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Gmail attachment data is malformed base64url") from exc
