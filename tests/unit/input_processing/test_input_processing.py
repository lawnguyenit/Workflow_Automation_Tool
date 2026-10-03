from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from workflow_automation.contracts import ExecutionContext, ResultState, RunMode, Retryability
from workflow_automation.modules.input.email_ingest import EmailIngest
from workflow_automation.modules.processing.document_extract import DocumentExtract


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "fixtures"


def context(mode: RunMode = RunMode.MOCK) -> ExecutionContext:
    return ExecutionContext(workflow_id="intake-test", mode=mode)


def fixture_items(name: str) -> list[dict]:
    path = FIXTURES / "email" / "emails.json"
    return json.loads(path.read_text(encoding="utf-8"))["fixtures"][name]


def attachment_from_pdf(name: str = "synthetic_invoice.pdf", *, mime: str = "application/pdf") -> dict:
    content = (FIXTURES / "pdf" / name).read_bytes()
    return {
        "attachment_id": f"fixture-{name}",
        "filename": name,
        "mime_type": mime,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def test_fixture_ingest_preserves_duplicate_provider_identity_and_order() -> None:
    result = EmailIngest().execute({"source": "fixture", "fixture_id": "synthetic_duplicate"}, context())

    assert result.status == ResultState.SUCCESS
    items = result.business_result["items"]
    assert [item["message_id"] for item in items] == ["fixture-message-dup"] * 2
    assert {item["source_system"] for item in items} == {"gmail_fixture"}
    assert {item["source_account"] for item in items} == {"fixture:synthetic_duplicate"}
    assert [item["attachments"][0]["attachment_id"] for item in items] == [
        "same-provider-attachment-id", "same-provider-attachment-id"
    ]


def test_fixture_without_attachments_is_a_successful_empty_input() -> None:
    result = EmailIngest().execute(
        {"source": "fixture", "fixture_id": "synthetic_no_attachment"}, context()
    )

    assert result.status == ResultState.SUCCESS
    assert result.business_result["items"][0]["attachments"] == []


def test_gmail_http_mapping_handles_nested_mime_and_base64url(monkeypatch) -> None:
    pdf = (FIXTURES / "pdf" / "synthetic_invoice.pdf").read_bytes()
    url_data = base64.urlsafe_b64encode(pdf).decode("ascii").rstrip("=")
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/users/me/messages"):
            return httpx.Response(200, json={"messages": [{"id": "provider-msg-9"}]})
        if request.url.path.endswith("/users/me/messages/provider-msg-9"):
            return httpx.Response(200, json={
                "id": "provider-msg-9", "threadId": "provider-thread-4",
                "payload": {"headers": [
                    {"name": "Subject", "value": "Synthetic PDF"},
                    {"name": "From", "value": "docs@example.invalid"},
                ], "parts": [{"mimeType": "multipart/mixed", "parts": [{
                    "filename": "synthetic_invoice.pdf", "mimeType": "application/pdf",
                    "body": {"attachmentId": "provider-att-7", "size": len(pdf)},
                }]}]},
            })
        if request.url.path.endswith("/attachments/provider-att-7"):
            return httpx.Response(200, json={"data": url_data, "size": len(pdf)})
        return httpx.Response(404)

    monkeypatch.setenv("GOOGLE_GMAIL_ACCESS_TOKEN", "test-secret-token")
    module = EmailIngest(transport=httpx.MockTransport(handler), api_base_url="https://gmail.test")
    result = module.execute({"source": "gmail", "source_account": "work-mailbox-1",
                             "query": "has:attachment", "max_results": 4},
                            context(RunMode.REAL))

    assert result.status == ResultState.SUCCESS
    item = result.business_result["items"][0]
    assert (item["message_id"], item["thread_id"]) == ("provider-msg-9", "provider-thread-4")
    assert (item["source_system"], item["source_account"]) == ("gmail", "work-mailbox-1")
    assert item["subject"] == "Synthetic PDF"
    assert base64.b64decode(item["attachments"][0]["content_base64"]) == pdf
    assert all(method == "GET" for method, _ in requests)


def test_gmail_http_classifies_auth_rate_and_transient_errors(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_GMAIL_ACCESS_TOKEN", "never-echo-secret")
    expectations = [(401, "AUTHENTICATION", Retryability.NOT_RETRYABLE),
                    (429, "RATE_LIMIT", Retryability.RETRYABLE),
                    (503, "TRANSIENT", Retryability.RETRYABLE)]
    for status_code, category, retryability in expectations:
        module = EmailIngest(
            transport=httpx.MockTransport(lambda request, code=status_code: httpx.Response(code)),
            api_base_url="https://gmail.test",
        )
        result = module.execute({"source": "gmail", "source_account": "test-account"}, context(RunMode.REAL))
        assert result.status == ResultState.FAILURE
        assert result.errors[0].category == category
        assert result.errors[0].retryability == retryability
        assert "never-echo-secret" not in result.model_dump_json()


def test_gmail_bounds_count_and_attachment_bytes(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_GMAIL_ACCESS_TOKEN", "token")
    module = EmailIngest(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"messages": []})),
        api_base_url="https://gmail.test",
    )
    bad_count = module.execute({"source": "gmail", "source_account": "test-account", "max_results": 51},
                               context(RunMode.REAL))
    assert bad_count.errors[0].code == "INVALID_MAX_RESULTS"
    missing_account = module.execute({"source": "gmail"}, context(RunMode.REAL))
    assert missing_account.errors[0].code == "SOURCE_ACCOUNT_REQUIRED"
    email_account = module.execute({"source": "gmail", "source_account": "user@example.invalid"},
                                   context(RunMode.REAL))
    assert email_account.errors[0].code == "SOURCE_ACCOUNT_REQUIRED"

    pdf = (FIXTURES / "pdf" / "synthetic_invoice.pdf").read_bytes()
    result = DocumentExtract(max_file_bytes=32).execute({"items": [{
        "message_id": "m1", "source_system": "gmail_fixture", "source_account": "fixture:test",
        "attachments": [{
            "attachment_id": "a1", "filename": "large.pdf", "mime_type": "application/pdf",
            "content_base64": base64.b64encode(pdf).decode("ascii"),
        }],
    }]}, context())
    assert result.status == ResultState.REVIEW_REQUIRED
    assert result.business_result["documents"][0]["error_code"] == "FILE_TOO_LARGE"


def test_gmail_omits_attachments_when_batch_byte_budget_is_exhausted(monkeypatch) -> None:
    pdf = (FIXTURES / "pdf" / "synthetic_invoice.pdf").read_bytes()
    encoded = base64.urlsafe_b64encode(pdf).decode("ascii").rstrip("=")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages"):
            return httpx.Response(200, json={"messages": [{"id": "message-budget"}]})
        if request.url.path.endswith("/users/me/messages/message-budget"):
            return httpx.Response(200, json={
                "id": "message-budget", "threadId": "thread-budget",
                "payload": {"parts": [{"filename": "invoice.pdf", "mimeType": "application/pdf",
                                        "body": {"attachmentId": "attachment-budget", "size": len(pdf)}}]},
            })
        if request.url.path.endswith("/attachments/attachment-budget"):
            return httpx.Response(200, json={"data": encoded})
        return httpx.Response(404)

    monkeypatch.setenv("GOOGLE_GMAIL_ACCESS_TOKEN", "token")
    result = EmailIngest(
        transport=httpx.MockTransport(handler), api_base_url="https://gmail.test",
        max_attachment_bytes=1024, max_total_attachment_bytes=20,
    ).execute({"source": "gmail", "source_account": "test-account"}, context(RunMode.REAL))
    assert result.status == ResultState.PARTIAL
    assert result.business_result["items"][0]["attachments"] == []
    assert result.metrics["attachments_omitted"] == 1


def test_document_extract_reads_pdf_and_preserves_provider_provenance() -> None:
    item = fixture_items("synthetic_invoice")[0]
    result = DocumentExtract().execute({"items": [item]}, context())

    assert result.status == ResultState.SUCCESS
    document = result.business_result["documents"][0]
    assert document["source_message_id"] == "fixture-message-001"
    assert document["source_attachment_id"] == "fixture-attachment-001"
    assert document["source_system"] == "gmail_fixture"
    assert document["source_account"] == "fixture:synthetic_invoice"
    assert document["source_ids"] == ["message:fixture-message-001", "attachment:fixture-attachment-001"]
    assert "INV-1001" in document["text"]
    assert len(document["sha256"]) == 64
    assert document["page_count"] == 1


def test_document_extract_retains_duplicate_documents_in_input_order() -> None:
    items = fixture_items("synthetic_duplicate")
    result = DocumentExtract().execute({"items": items}, context())

    documents = result.business_result["documents"]
    assert len(documents) == 2
    assert documents[0]["sha256"] == documents[1]["sha256"]
    assert documents[0]["source_message_id"] == documents[1]["source_message_id"]


def test_document_extract_reports_no_attachment_as_empty_success() -> None:
    result = DocumentExtract().execute({"items": fixture_items("synthetic_no_attachment")}, context())
    assert result.status == ResultState.SUCCESS
    assert result.business_result == {"documents": []}


def test_document_extract_review_cases_are_explicit_and_content_is_not_echoed() -> None:
    malformed = attachment_from_pdf("synthetic_corrupt.pdf")
    oversized = attachment_from_pdf()
    image_only = attachment_from_pdf("synthetic_image_only.pdf")
    unsupported = attachment_from_pdf("synthetic_invoice.pdf")
    unsupported["filename"] = "archive.zip"
    unsupported["mime_type"] = "application/zip"
    cases = [
        (malformed, "MALFORMED_PDF"),
        (image_only, "OCR_REQUIRED"),
        (unsupported, "UNSUPPORTED_DOCUMENT_TYPE"),
    ]
    for attachment, code in cases:
        result = DocumentExtract().execute({"items": [{
            "message_id": "m", "source_system": "gmail_fixture", "source_account": "fixture:test",
            "attachments": [attachment],
        }]}, context())
        assert result.status == ResultState.REVIEW_REQUIRED
        document = result.business_result["documents"][0]
        assert document["error_code"] == code
        assert document["text"] == ""

    oversized_result = DocumentExtract(max_file_bytes=32).execute(
        {"items": [{"message_id": "m", "source_system": "gmail_fixture",
                     "source_account": "fixture:test", "attachments": [oversized]}]}, context()
    )
    assert oversized_result.business_result["documents"][0]["error_code"] == "FILE_TOO_LARGE"


def test_document_extract_rejects_malformed_base64_and_mime_mismatch() -> None:
    malformed = {"attachment_id": "a", "filename": "invoice.pdf", "mime_type": "application/pdf",
                 "content_base64": "%%%"}
    mismatch = attachment_from_pdf(mime="text/plain")
    result = DocumentExtract().execute({"items": [{"message_id": "m", "source_system": "gmail_fixture",
                                                    "source_account": "fixture:test",
                                                    "attachments": [malformed, mismatch]}]},
                                       context())
    assert result.status == ResultState.REVIEW_REQUIRED
    assert [doc["error_code"] for doc in result.business_result["documents"]] == [
        "MALFORMED_BASE64", "MIME_TYPE_MISMATCH"
    ]
