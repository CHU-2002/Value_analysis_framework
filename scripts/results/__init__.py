"""Shared result protocol and context-building utilities."""

from .schema import (
    RESULT_SCHEMA,
    RESULT_SCHEMA_ID,
    RESULT_SCHEMA_VERSION,
    ResultValidationError,
    compact_result,
    validate_result,
)

__all__ = [
    "RESULT_SCHEMA",
    "RESULT_SCHEMA_ID",
    "RESULT_SCHEMA_VERSION",
    "ResultValidationError",
    "compact_result",
    "validate_result",
]
