"""Errors that carry a user-readable message. The frontend shows `detail` verbatim."""
from __future__ import annotations


class ApiError(Exception):
    status_code = 400

    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class ValidationFailed(ApiError):
    status_code = 422


class NotFound(ApiError):
    status_code = 404


class ArchiveUnavailable(ApiError):
    status_code = 503

    def __init__(self, detail: str = "The archive is temporarily unavailable. Please retry."):
        super().__init__(detail)


class NoCoverage(ApiError):
    status_code = 404

    def __init__(self, detail: str = "No SPHEREx observations cover this sky position."):
        super().__init__(detail)
