"""Reusable OpenAPI response metadata for the canonical error envelope.

The exception handlers are the runtime authority.  These entries only describe
that already-existing wire format in the operations that are being aligned in
Phase 3 Step 11B; they do not install handlers or change status codes.
"""

from app.schemas.common import ErrorResponse


_ERROR_DESCRIPTIONS = {
    401: "Authentication required; returns the canonical error envelope.",
    403: "The authenticated caller lacks the required permission; returns the canonical error envelope.",
    404: "The requested media object or product was not found; returns the canonical error envelope.",
    422: "Request validation or business-rule failure; returns the canonical error envelope.",
    500: "Unexpected server failure; returns the canonical error envelope.",
}


def canonical_error_responses(*status_codes: int) -> dict:
    """Build OpenAPI response metadata using the shared canonical error DTO."""
    return {
        status_code: {
            "model": ErrorResponse,
            "description": _ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }
