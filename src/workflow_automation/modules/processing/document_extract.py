from __future__ import annotations

import base64
import binascii
import hashlib
import io
from typing import Any

from pypdf import PdfReader

from workflow_automation.contracts import (
    ExecutionContext,
    ModuleError,
    ModuleResult,
    ResultState,
    Retryability,
)


MODULE_ID = "document.extract"
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_TEXT_CHARS = 500_000
PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}


class DocumentExtract:
    """Extract bounded, machine-readable text from PDF email attachments.

    This implementation does not perform OCR. Image-only PDFs, unsupported file
    types, encrypted files, malformed PDFs, and limit breaches are marked for
    review with safe error codes; their raw content is never logged or returned.
    Duplicate attachments are preserved in input order and identified by source
    message/attachment IDs plus a SHA-256 content digest.
    """

    module_id = MODULE_ID

    def __init__(
        self,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    ) -> None:
        if not 0 < max_file_bytes <= DEFAULT_MAX_FILE_BYTES:
            raise ValueError(f"max_file_bytes must be from 1 to {DEFAULT_MAX_FILE_BYTES}")
        if not 0 < max_pages <= DEFAULT_MAX_PAGES:
            raise ValueError(f"max_pages must be from 1 to {DEFAULT_MAX_PAGES}")
        if not 0 < max_text_chars <= DEFAULT_MAX_TEXT_CHARS:
            raise ValueError(f"max_text_chars must be from 1 to {DEFAULT_MAX_TEXT_CHARS}")
        self.max_file_bytes = max_file_bytes
        self.max_pages = max_pages
        self.max_text_chars = max_text_chars

    def execute(self, payload: dict[str, Any], context: ExecutionContext) -> ModuleResult:
        items = payload.get("items")
        if not isinstance(items, list):
            return self._failure(context, "INVALID_INPUT", "VALIDATION",
                                 "items must be a list of ingested email messages",
                                 Retryability.NOT_RETRYABLE)

        documents: list[dict[str, Any]] = []
        errors: list[ModuleError] = []
        warnings: list[str] = []
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(self._error("INVALID_MESSAGE", "VALIDATION",
                                          "An email item is not an object", Retryability.NOT_RETRYABLE))
                continue
            attachments = item.get("attachments", [])
            if not isinstance(attachments, list):
                errors.append(self._error("INVALID_ATTACHMENTS", "VALIDATION",
                                          "Email attachments must be a list", Retryability.NOT_RETRYABLE))
                continue
            for attachment in attachments:
                document, error, warning = self._extract_one(item, attachment)
                if document is not None:
                    documents.append(document)
                if error is not None:
                    errors.append(error)
                if warning:
                    warnings.append(warning)

        if any(error.category == "REVIEW" for error in errors):
            status = ResultState.REVIEW_REQUIRED
        elif errors and documents:
            status = ResultState.PARTIAL
        elif errors:
            status = ResultState.FAILURE
        else:
            status = ResultState.SUCCESS
        return ModuleResult(
            module_id=MODULE_ID,
            status=status,
            business_result={"documents": documents},
            errors=errors,
            warnings=warnings,
            metrics={"documents_received": sum(
                len(m.get("attachments", [])) for m in items if isinstance(m, dict)
                and isinstance(m.get("attachments", []), list)
            ), "documents_extracted": sum(doc.get("status") == "SUCCESS" for doc in documents)},
            context=context,
        ).complete()

    def _extract_one(
        self,
        message: dict[str, Any],
        attachment: Any,
    ) -> tuple[dict[str, Any] | None, ModuleError | None, str | None]:
        if not isinstance(attachment, dict):
            return None, self._error("INVALID_ATTACHMENT", "VALIDATION",
                                     "An attachment is not an object", Retryability.NOT_RETRYABLE), None
        message_id = message.get("message_id")
        source_system = message.get("source_system")
        source_account = message.get("source_account")
        attachment_id = attachment.get("attachment_id")
        filename = attachment.get("filename")
        mime_type = attachment.get("mime_type")
        if not all(isinstance(value, str) and value for value in
                   (message_id, source_system, source_account, attachment_id, filename, mime_type)):
            return None, self._error("MISSING_PROVENANCE", "VALIDATION",
                                     "Message and attachment IDs, filename, and MIME type are required",
                                     Retryability.NOT_RETRYABLE), None
        if mime_type.casefold() not in PDF_MIME_TYPES and not filename.casefold().endswith(".pdf"):
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "UNSUPPORTED_DOCUMENT_TYPE",
                                    "This extractor supports PDF files only",
                                    source_system=source_system, source_account=source_account)
        if mime_type.casefold() not in PDF_MIME_TYPES and filename.casefold().endswith(".pdf"):
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "MIME_TYPE_MISMATCH",
                                    "The file name indicates PDF but its MIME type does not",
                                    source_system=source_system, source_account=source_account)
        encoded = attachment.get("content_base64")
        if not isinstance(encoded, str):
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "MISSING_CONTENT", "Attachment has no inline base64 content",
                                    source_system=source_system, source_account=source_account)
        # Reject obviously oversized values before allocating decoded file bytes.
        if len(encoded) > ((self.max_file_bytes + 2) // 3) * 4 + 4:
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "FILE_TOO_LARGE", "Attachment exceeds the configured size limit",
                                    source_system=source_system, source_account=source_account)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "MALFORMED_BASE64", "Attachment content is malformed base64",
                                    source_system=source_system, source_account=source_account)
        if len(raw) > self.max_file_bytes:
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "FILE_TOO_LARGE", "Attachment exceeds the configured size limit",
                                    source_system=source_system, source_account=source_account)
        digest = hashlib.sha256(raw).hexdigest()
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
            if reader.is_encrypted:
                return self._review_doc(message_id, attachment_id, filename, mime_type,
                                        "ENCRYPTED_PDF", "Encrypted PDFs require manual review", digest,
                                        source_system=source_system, source_account=source_account)
            if len(reader.pages) > self.max_pages:
                return self._review_doc(message_id, attachment_id, filename, mime_type,
                                        "PAGE_LIMIT_EXCEEDED", "PDF exceeds the configured page limit", digest,
                                        source_system=source_system, source_account=source_account)
            page_text = [page.extract_text() or "" for page in reader.pages]
        except Exception:
            # Parser exceptions may include snippets of document content; keep them out of results.
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "MALFORMED_PDF", "PDF could not be parsed", digest,
                                    source_system=source_system, source_account=source_account)

        text = "\n\n".join(page_text).strip()
        if not text:
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "OCR_REQUIRED", "No selectable text was found; OCR is unavailable", digest,
                                    source_system=source_system, source_account=source_account)
        if len(text) > self.max_text_chars:
            return self._review_doc(message_id, attachment_id, filename, mime_type,
                                    "TEXT_LIMIT_EXCEEDED", "Extracted text exceeds the configured limit", digest,
                                    source_system=source_system, source_account=source_account)
        return ({
            "source_message_id": message_id,
            "source_attachment_id": attachment_id,
            "source_system": source_system,
            "source_account": source_account,
            "source_ids": [f"message:{message_id}", f"attachment:{attachment_id}"],
            "filename": filename,
            "mime_type": mime_type,
            "text": text,
            "sha256": digest,
            "page_count": len(reader.pages),
            "status": "SUCCESS",
        }, None, None)

    def _review_doc(
        self,
        message_id: str,
        attachment_id: str,
        filename: str,
        mime_type: str,
        code: str,
        message: str,
        digest: str | None = None,
        *,
        source_system: str,
        source_account: str,
    ) -> tuple[dict[str, Any], ModuleError, None]:
        document = {
            "source_message_id": message_id,
            "source_attachment_id": attachment_id,
            "source_system": source_system,
            "source_account": source_account,
            "source_ids": [f"message:{message_id}", f"attachment:{attachment_id}"],
            "filename": filename,
            "mime_type": mime_type,
            "text": "",
            "sha256": digest,
            "page_count": None,
            "status": "REVIEW_REQUIRED",
            "error_code": code,
        }
        error = self._error(code, "REVIEW", message, Retryability.NOT_RETRYABLE,
                            details={"source_message_id": message_id,
                                     "source_attachment_id": attachment_id})
        return document, error, None

    @staticmethod
    def _error(
        code: str,
        category: str,
        message: str,
        retryability: Retryability,
        *,
        details: dict[str, Any] | None = None,
    ) -> ModuleError:
        return ModuleError(code=code, category=category, message=message,
                           retryability=retryability, details=details or {})

    @staticmethod
    def _failure(
        context: ExecutionContext,
        code: str,
        category: str,
        message: str,
        retryability: Retryability,
    ) -> ModuleResult:
        return ModuleResult(module_id=MODULE_ID, status=ResultState.FAILURE,
                            errors=[DocumentExtract._error(code, category, message, retryability)],
                            context=context).complete()
