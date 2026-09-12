import pytest

from core.urban_generator.domain import (
    CancelledError,
    ConfigError,
    DataError,
    DomainError,
    ErrorCategory,
    ErrorCode,
    ErrorDescriptor,
    PermanentError,
    TransientError,
    UrbanGeneratorError,
)


@pytest.mark.parametrize(
    ("error_type", "code", "category", "retryable", "cancelled"),
    [
        (DomainError, ErrorCode.DOMAIN_ERROR, ErrorCategory.DOMAIN, False, False),
        (ConfigError, ErrorCode.CONFIG_ERROR, ErrorCategory.CONFIG, False, False),
        (DataError, ErrorCode.DATA_ERROR, ErrorCategory.DATA, False, False),
        (TransientError, ErrorCode.TRANSIENT_ERROR, ErrorCategory.TRANSIENT, True, False),
        (PermanentError, ErrorCode.PERMANENT_ERROR, ErrorCategory.PERMANENT, False, False),
        (CancelledError, ErrorCode.CANCELLED_ERROR, ErrorCategory.CANCELLED, False, True),
    ],
)
def test_error_taxonomy_exposes_stable_machine_metadata(
    error_type: type[UrbanGeneratorError],
    code: ErrorCode,
    category: ErrorCategory,
    retryable: bool,
    cancelled: bool,
) -> None:
    error = error_type("same human message", details={"source": "unit-test"})

    assert error.code is code
    assert error.category is category
    assert error.retryable is retryable
    assert error.cancelled is cancelled
    assert error.descriptor == ErrorDescriptor(
        code=code,
        category=category,
        retryable=retryable,
        cancelled=cancelled,
    )
    assert error.details == {"source": "unit-test"}


def test_backend_or_worker_can_map_error_without_parsing_message() -> None:
    handlers = {
        ErrorCode.DOMAIN_ERROR: "reject",
        ErrorCode.TRANSIENT_ERROR: "retry",
        ErrorCode.CANCELLED_ERROR: "cancelled",
    }

    assert handlers[DomainError("identical text").code] == "reject"
    assert handlers[TransientError("identical text").code] == "retry"
    assert handlers[CancelledError("identical text").code] == "cancelled"


def test_error_details_are_immutable() -> None:
    error = DataError("invalid dataset", details={"dataset": "roads"})

    with pytest.raises(TypeError):
        error.details["dataset"] = "buildings"  # type: ignore[index]


def test_error_message_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        PermanentError("   ")
