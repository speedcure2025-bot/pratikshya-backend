from typing import Any, Dict, List, Optional, Union

# `details` is rendered verbatim into the canonical error envelope
# (`{success, error:{code, message, details}}`). Most business errors describe
# themselves with a mapping; request-shaped validation errors use the
# FastAPI field-error LIST (`[{loc, msg, type, …}]`) so a service-raised 422
# is indistinguishable, to the client, from a schema-raised one.
ErrorDetails = Union[Dict[str, Any], List[Any]]


class AppException(Exception):
    """Base application exception class."""
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: Optional[str] = None,
        details: Optional[ErrorDetails] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or "BAD_REQUEST"
        # An empty LIST is a legitimate details payload (field-error shape);
        # only `None` falls back to the empty mapping.
        self.details = {} if details is None else details
        super().__init__(message)


class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=404, error_code="NOT_FOUND", details=details)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Authentication required", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=401, error_code="UNAUTHORIZED", details=details)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Permission denied", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=403, error_code="FORBIDDEN", details=details)


class ConflictException(AppException):
    def __init__(self, message: str = "Resource conflict", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=409, error_code="CONFLICT", details=details)


class BusinessLogicException(AppException):
    def __init__(self, message: str, details: Optional[ErrorDetails] = None):
        super().__init__(message=message, status_code=422, error_code="BUSINESS_RULE_VIOLATION", details=details)


class ValidationException(AppException):
    """
    HTTP 422 `VALIDATION_ERROR` raised from the service layer.

    The Phase 1 envelope is unchanged — this only lets a rule that can be
    decided ONLY against the database (e.g. "does this category exist and may
    it be assigned?") answer with exactly the same `code`/`details` contract
    the Pydantic/`RequestValidationError` handler emits, instead of leaking a
    500 or inventing a second format. `details` is therefore the FastAPI
    field-error list: `[{"loc": ["body", "<field>"], "msg": …, "type": …}]`.
    """

    def __init__(
        self,
        message: str = "Invalid request payload or parameters",
        details: Optional[ErrorDetails] = None,
    ):
        super().__init__(
            message=message,
            status_code=422,
            error_code="VALIDATION_ERROR",
            details=details if details is not None else [],
        )


class TooManyRequestsException(AppException):
    def __init__(self, message: str = "Too many requests. Please slow down.", details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, status_code=429, error_code="RATE_LIMIT_EXCEEDED", details=details)
