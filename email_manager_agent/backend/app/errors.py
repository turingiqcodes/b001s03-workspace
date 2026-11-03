"""Error types shared by the whole backend.

Every error carries a short machine-readable `code` and a readable `message` that is
safe to show the user. Messages never contain email bodies or secrets.
"""


class AppError(Exception):
    """Base class for errors the API turns into a JSON error response."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class ConfigurationError(AppError):
    code = "configuration_error"
    http_status = 503


class ValidationFailed(AppError):
    """Input from the user, the frontend or Claude failed validation."""

    code = "validation_error"
    http_status = 422


class BridgeError(AppError):
    """The Apps Script bridge returned an error or could not be reached."""

    code = "bridge_error"
    http_status = 502


class BridgeTimeout(BridgeError):
    code = "bridge_timeout"
    http_status = 504


class ModelError(AppError):
    """The language model (Ollama or Claude) failed to answer."""

    code = "model_error"
    http_status = 502


class ApprovalError(AppError):
    """An approval request did not match a valid pending operation."""

    code = "approval_error"
    http_status = 409


class NotFound(AppError):
    code = "not_found"
    http_status = 404
