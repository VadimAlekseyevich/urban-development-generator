from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Mapping


class ErrorCategory(StrEnum):
    """Stable high-level categories used by backend and worker error mapping."""

    DOMAIN = "domain"
    CONFIG = "config"
    DATA = "data"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    CANCELLED = "cancelled"


class ErrorCode(StrEnum):
    """Stable machine-readable codes for the base error taxonomy."""

    DOMAIN_ERROR = "domain.error"
    CONFIG_ERROR = "config.error"
    DATA_ERROR = "data.error"
    TRANSIENT_ERROR = "transient.error"
    PERMANENT_ERROR = "permanent.error"
    CANCELLED_ERROR = "cancelled.error"


@dataclass(frozen=True, slots=True)
class ErrorDescriptor:
    """Transport-neutral metadata for mapping an error without parsing its message."""

    code: ErrorCode
    category: ErrorCategory
    retryable: bool
    cancelled: bool


class UrbanGeneratorError(Exception):
    """Base class for stable, machine-readable application/domain errors."""

    code: ClassVar[ErrorCode]
    category: ClassVar[ErrorCategory]
    retryable: ClassVar[bool] = False
    cancelled: ClassVar[bool] = False

    def __init__(self, message: str, *, details: Mapping[str, str] | None = None) -> None:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("error message must not be blank")

        self.message = normalized_message
        self.details: Mapping[str, str] = MappingProxyType(dict(details or {}))
        super().__init__(normalized_message)

    @property
    def descriptor(self) -> ErrorDescriptor:
        return ErrorDescriptor(
            code=self.code,
            category=self.category,
            retryable=self.retryable,
            cancelled=self.cancelled,
        )


class DomainError(UrbanGeneratorError):
    """A domain rule or invariant was violated."""

    code = ErrorCode.DOMAIN_ERROR
    category = ErrorCategory.DOMAIN


class ConfigError(UrbanGeneratorError):
    """Configuration is invalid or incompatible with the requested operation."""

    code = ErrorCode.CONFIG_ERROR
    category = ErrorCategory.CONFIG


class DataError(UrbanGeneratorError):
    """Input data is invalid, inconsistent, or insufficient for the operation."""

    code = ErrorCode.DATA_ERROR
    category = ErrorCategory.DATA


class TransientError(UrbanGeneratorError):
    """A retry may succeed without changing the request."""

    code = ErrorCode.TRANSIENT_ERROR
    category = ErrorCategory.TRANSIENT
    retryable = True


class PermanentError(UrbanGeneratorError):
    """The operation failed permanently and should not be retried unchanged."""

    code = ErrorCode.PERMANENT_ERROR
    category = ErrorCategory.PERMANENT


class CancelledError(UrbanGeneratorError):
    """The operation stopped because cancellation was requested."""

    code = ErrorCode.CANCELLED_ERROR
    category = ErrorCategory.CANCELLED
    cancelled = True
