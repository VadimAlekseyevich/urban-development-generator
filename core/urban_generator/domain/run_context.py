import hashlib
import uuid
from dataclasses import dataclass

from numpy.random import Generator, PCG64

from core.urban_generator.domain.crs import WorkingCRS, require_working_crs
from core.urban_generator.domain.semantics import RunMode


class RunContextError(ValueError):
    """Raised when a generation run context violates its domain contract."""


_MAX_SEED = (1 << 64) - 1
_MAX_NAMESPACE_BYTES = 256


@dataclass(frozen=True, slots=True)
class ConfigRef:
    """Opaque immutable reference to one versioned run configuration."""

    name: str
    ref: str

    def __post_init__(self) -> None:
        _require_token("config name", self.name)
        _require_token("config ref", self.ref)


@dataclass(frozen=True, slots=True)
class CorrelationMetadata:
    """Infrastructure-neutral identifiers propagated through logs and jobs."""

    correlation_id: str
    request_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        _require_token("correlation_id", self.correlation_id)
        _require_optional_token("request_id", self.request_id)
        _require_optional_token("job_id", self.job_id)


@dataclass(frozen=True, slots=True)
class DeterministicRNGFactory:
    """Creates order-independent NumPy RNG streams from one run seed."""

    seed: int

    def __post_init__(self) -> None:
        _require_seed(self.seed)

    def derive_seed(self, namespace: str = "run") -> int:
        namespace_value = _require_token("RNG namespace", namespace)
        namespace_bytes = namespace_value.encode("utf-8")
        if len(namespace_bytes) > _MAX_NAMESPACE_BYTES:
            raise RunContextError(
                f"RNG namespace must be at most {_MAX_NAMESPACE_BYTES} UTF-8 bytes"
            )

        payload = (
            b"urban-development-generator:rng:v1\0"
            + self.seed.to_bytes(8, "big", signed=False)
            + len(namespace_bytes).to_bytes(2, "big", signed=False)
            + namespace_bytes
        )
        digest = hashlib.blake2b(payload, digest_size=16).digest()
        return int.from_bytes(digest, "big", signed=False)

    def create(self, namespace: str = "run") -> Generator:
        """Return a fresh deterministic stream for the requested namespace."""

        return Generator(PCG64(self.derive_seed(namespace)))


@dataclass(frozen=True, slots=True)
class RunContext:
    """Immutable execution context shared by future generation stages."""

    run_id: uuid.UUID
    mode: RunMode
    seed: int
    working_srid: int
    config_refs: tuple[ConfigRef, ...]
    correlation: CorrelationMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, uuid.UUID):
            raise RunContextError("run_id must be a UUID")
        if not isinstance(self.mode, RunMode):
            raise RunContextError("mode must be a RunMode value")
        _require_seed(self.seed)
        require_working_crs(self.working_srid)

        if not isinstance(self.config_refs, tuple):
            raise RunContextError("config_refs must be an immutable tuple")
        names: set[str] = set()
        for config_ref in self.config_refs:
            if not isinstance(config_ref, ConfigRef):
                raise RunContextError("config_refs must contain only ConfigRef values")
            if config_ref.name in names:
                raise RunContextError(f"duplicate config ref name: {config_ref.name}")
            names.add(config_ref.name)

        if not isinstance(self.correlation, CorrelationMetadata):
            raise RunContextError("correlation must be CorrelationMetadata")

    @property
    def working_crs(self) -> WorkingCRS:
        return require_working_crs(self.working_srid)

    @property
    def rng_factory(self) -> DeterministicRNGFactory:
        return DeterministicRNGFactory(self.seed)

    def rng_seed(self, namespace: str = "run") -> int:
        return self.rng_factory.derive_seed(namespace)

    def rng(self, namespace: str = "run") -> Generator:
        return self.rng_factory.create(namespace)


def _require_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise RunContextError("seed must be an integer")
    if seed < 0 or seed > _MAX_SEED:
        raise RunContextError(f"seed must be between 0 and {_MAX_SEED}")


def _require_token(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunContextError(f"{field_name} must be a non-empty string")
    return value


def _require_optional_token(field_name: str, value: str | None) -> None:
    if value is not None:
        _require_token(field_name, value)
