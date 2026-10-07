from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra

    def to_body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class NotReady(AppError):
    status_code = 409
    code = "file_not_ready"


class FileTooLarge(AppError):
    status_code = 413
    code = "file_too_large"


class UnsupportedFileType(AppError):
    status_code = 415
    code = "unsupported_file_type"


class InvalidArchive(AppError):
    status_code = 422
    code = "invalid_upload"


class ProcessingError(AppError):
    status_code = 422
    code = "processing_failed"


class InternalProcessingError(AppError):
    status_code = 500
    code = "internal_error"
