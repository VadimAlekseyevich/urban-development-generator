"""Strict versioned decoding of persisted canonical per-Stage configuration.

First P0/#202 slice: this builds typed *existing* StageConfig dataclasses
from JSONB, not execution bindings, live GIS input adapters or a production
worker runtime. The canonical catalog has twelve identities but presently
versioned subsets of concrete Stage implementations with typed configs; unimplemented
stage identities are intentionally rejected rather than assigned fake stages.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from core.urban_generator.domain import ConfigRef, ResolvedConfigBinding
from core.urban_generator.stages import (
    CANONICAL_STAGE_ORDER,
    BlocksAndParcelsStage,
    BlocksAndParcelsStageConfig,
    BuildingStage,
    BuildingStageConfig,
    ConstraintMaskStage,
    ConstraintMaskStageConfig,
    DemographyStage,
    DemographyStageConfig,
    PrepareSnapshotStage,
    PrepareSnapshotStageConfig,
    RoadStage,
    RoadStageConfig,
    SuitabilityStage,
    SuitabilityStageConfig,
    ZoningStage,
    ZoningStageConfig,
)

CONFIG_SCHEMA_VERSION = "canonical-stage-configs-v1"
PREPARED_CONFIG_SCHEMA_VERSION = "canonical-stage-configs-v2"
MAX_CONFIG_BYTES = 1_048_576
MAX_COLLECTION_ITEMS = 2048
MAX_NESTED_DEPTH = 24

# Identity, version and config type all come from the *existing* Stage
# implementations; this catalog never adds a second Stage or a no-op placeholder.
_CONFIG_TYPES: dict[str, tuple[str, type[Any]]] = {
    ConstraintMaskStage.name: (ConstraintMaskStage.version, ConstraintMaskStageConfig),
    SuitabilityStage.name: (SuitabilityStage.version, SuitabilityStageConfig),
    ZoningStage.name: (ZoningStage.version, ZoningStageConfig),
    RoadStage.name: (RoadStage.version, RoadStageConfig),
    BlocksAndParcelsStage.name: (
        BlocksAndParcelsStage.version, BlocksAndParcelsStageConfig
    ),
    BuildingStage.name: (BuildingStage.version, BuildingStageConfig),
    DemographyStage.name: (DemographyStage.version, DemographyStageConfig),
}
SUPPORTED_CONFIG_STAGE_NAMES = tuple(
    name for name in CANONICAL_STAGE_ORDER if name in _CONFIG_TYPES
)


_PREPARED_CONFIG_TYPES = {
    **_CONFIG_TYPES,
    PrepareSnapshotStage.name: (PrepareSnapshotStage.version, PrepareSnapshotStageConfig),
}


class PersistedStageConfigError(ValueError):
    """Untrusted persisted JSON cannot become a typed canonical stage config."""


def _plain_object(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise PersistedStageConfigError(f"{path} must be a JSON object")
    return value


def _exact_keys(
    value: Mapping[str, object],
    *,
    path: str,
    required: frozenset[str],
) -> None:
    actual = frozenset(value)
    if actual != required:
        missing, unexpected = sorted(required - actual), sorted(actual - required)
        raise PersistedStageConfigError(
            f"{path} has missing keys {missing} and unexpected keys {unexpected}"
        )


def _decode(value: object, annotation: Any, *, path: str, depth: int) -> Any:
    """Translate the JSON type tree with strict, no-coercion semantics.

    No dynamic imports or class names are read from JSON. Only types reachable
    from the fixed canonical StageConfig catalog are constructed.
    """
    if depth > MAX_NESTED_DEPTH:
        raise PersistedStageConfigError(f"{path} exceeds nesting depth")
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        errors: list[str] = []
        for candidate in args:
            try:
                return _decode(value, candidate, path=path, depth=depth + 1)
            except PersistedStageConfigError as exc:
                errors.append(str(exc))
        raise PersistedStageConfigError(
            f"{path} matches no supported union variant: {'; '.join(errors[:3])}"
        )
    if origin is Literal:
        if any(
            value == expected and type(value) is type(expected) for expected in args
        ):
            return value
        raise PersistedStageConfigError(f"{path} must match a supported literal")
    if annotation is type(None):
        if value is None:
            return None
        raise PersistedStageConfigError(f"{path} must be null")
    if annotation is bool:
        if type(value) is bool:
            return value
        raise PersistedStageConfigError(f"{path} must be boolean")
    if annotation is str:
        if type(value) is str:
            return value
        raise PersistedStageConfigError(f"{path} must be text")
    if annotation is int:
        if type(value) is int:
            return value
        raise PersistedStageConfigError(f"{path} must be integer")
    if annotation is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                numeric = float(value)
            except OverflowError as exc:
                raise PersistedStageConfigError(
                    f"{path} must be finite number"
                ) from exc
            if math.isfinite(numeric):
                return numeric
        raise PersistedStageConfigError(f"{path} must be finite number")

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if type(value) is not str:
            raise PersistedStageConfigError(f"{path} must be enum text")
        try:
            return annotation(value)
        except ValueError as exc:
            raise PersistedStageConfigError(
                f"{path} has unsupported {annotation.__name__} value"
            ) from exc

    if origin is tuple:
        if not isinstance(value, list) or len(value) > MAX_COLLECTION_ITEMS:
            raise PersistedStageConfigError(
                f"{path} must be a bounded JSON array"
            )
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(
                _decode(item, args[0], path=f"{path}[{index}]", depth=depth + 1)
                for index, item in enumerate(value)
            )
        if len(value) != len(args):
            raise PersistedStageConfigError(
                f"{path} must contain exactly {len(args)} items"
            )
        return tuple(
            _decode(item, target, path=f"{path}[{index}]", depth=depth + 1)
            for index, (item, target) in enumerate(zip(value, args, strict=True))
        )
    if origin is frozenset:
        if not isinstance(value, list) or len(value) > MAX_COLLECTION_ITEMS:
            raise PersistedStageConfigError(
                f"{path} must be a bounded JSON array"
            )
        items = [
            _decode(item, args[0], path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
        try:
            result = frozenset(items)
        except TypeError as exc:
            raise PersistedStageConfigError(f"{path} contains unhashable items") from exc
        if len(result) != len(items):
            raise PersistedStageConfigError(f"{path} contains duplicate items")
        return result
    if origin is dict:
        raw = _plain_object(value, path=path)
        if len(raw) > MAX_COLLECTION_ITEMS or args[0] is not str:
            raise PersistedStageConfigError(f"{path} has unsupported object keys")
        return {
            key: _decode(item, args[1], path=f"{path}.{key}", depth=depth + 1)
            for key, item in raw.items()
        }

    if isinstance(annotation, type) and is_dataclass(annotation):
        raw = _plain_object(value, path=path)
        dataclass_fields = fields(annotation)
        known = {field.name for field in dataclass_fields if field.init}
        unexpected = set(raw) - known
        if unexpected:
            raise PersistedStageConfigError(
                f"{path} has unknown options {sorted(unexpected)}"
            )
        hints = get_type_hints(annotation)
        kwargs: dict[str, object] = {}
        for field in dataclass_fields:
            if not field.init:
                continue
            key = field.name
            if key not in raw:
                if field.default is MISSING and field.default_factory is MISSING:
                    raise PersistedStageConfigError(
                        f"{path} missing required option {key}"
                    )
                continue
            kwargs[key] = _decode(
                raw[key], hints[key], path=f"{path}.{key}", depth=depth + 1
            )
        try:
            return annotation(**kwargs)
        except (TypeError, ValueError) as exc:
            raise PersistedStageConfigError(
                f"{path} violates {annotation.__name__} core constraints: {exc}"
            ) from exc

    raise PersistedStageConfigError(
        f"{path} has unsupported typed configuration annotation {annotation!r}"
    )


def _annotation_schema(annotation: Any, *, depth: int = 0) -> dict[str, object]:
    """Emit JSON Schema matching the strict allowlisted dataclass decoder."""
    if depth > MAX_NESTED_DEPTH:
        raise PersistedStageConfigError("schema recursion depth exceeded")
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, UnionType):
        return {
            "anyOf": [
                _annotation_schema(option, depth=depth + 1) for option in args
            ]
        }
    if origin is Literal:
        return {"enum": list(args)}
    if annotation is type(None):
        return {"type": "null"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [item.value for item in annotation]}
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return {
                "type": "array",
                "items": _annotation_schema(args[0], depth=depth + 1),
                "maxItems": MAX_COLLECTION_ITEMS,
            }
        return {
            "type": "array",
            "prefixItems": [
                _annotation_schema(option, depth=depth + 1) for option in args
            ],
            "minItems": len(args),
            "maxItems": len(args),
        }
    if origin is frozenset:
        return {
            "type": "array",
            "items": _annotation_schema(args[0], depth=depth + 1),
            "uniqueItems": True,
            "maxItems": MAX_COLLECTION_ITEMS,
        }
    if origin is dict and args[0] is str:
        return {
            "type": "object",
            "additionalProperties": _annotation_schema(args[1], depth=depth + 1),
            "maxProperties": MAX_COLLECTION_ITEMS,
        }
    if isinstance(annotation, type) and is_dataclass(annotation):
        hints = get_type_hints(annotation)
        dataclass_fields = tuple(field for field in fields(annotation) if field.init)
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                field.name: _annotation_schema(
                    hints[field.name], depth=depth + 1
                ) for field in dataclass_fields
            },
            "required": [
                field.name for field in dataclass_fields
                if field.default is MISSING and field.default_factory is MISSING
            ],
        }
    raise PersistedStageConfigError(
        f"cannot generate safe JSON schema for {annotation!r}"
    )


class CanonicalStageConfigResolver:
    """Decode a sealed JSONB config into the existing, typed StageConfig objects."""

    def __init__(
        self, *, stage_names: tuple[str, ...] | None = None,
        schema_version: str = CONFIG_SCHEMA_VERSION,
    ) -> None:
        if schema_version not in (CONFIG_SCHEMA_VERSION, PREPARED_CONFIG_SCHEMA_VERSION):
            raise PersistedStageConfigError("unsupported persisted config_schema_version")
        self._schema_version = schema_version
        self._types = (
            _CONFIG_TYPES if schema_version == CONFIG_SCHEMA_VERSION else _PREPARED_CONFIG_TYPES
        )
        names = tuple(self._types) if stage_names is None else stage_names
        if (
            not isinstance(names, tuple)
            or not names
            or any(type(name) is not str or name not in self._types for name in names)
            or len(set(names)) != len(names)
        ):
            raise PersistedStageConfigError(
                "configured stages must be distinct concrete canonical stage names"
            )
        self._stage_names = tuple(
            name for name in CANONICAL_STAGE_ORDER if name in names
        )

    def resolve(
        self,
        *,
        config_json: Mapping[str, object],
        schema_version: str,
        source: ConfigRef,
    ) -> tuple[ResolvedConfigBinding, ...]:
        if schema_version != self._schema_version:
            raise PersistedStageConfigError(
                f"unsupported persisted config_schema_version: {schema_version}"
            )
        if not isinstance(source, ConfigRef):
            raise PersistedStageConfigError("source must be a canonical ConfigRef")
        raw = _plain_object(config_json, path="config_json")
        try:
            encoded = json.dumps(
                raw, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (ValueError, TypeError, OverflowError) as exc:
            raise PersistedStageConfigError(
                "persisted generation config must be finite JSON"
            ) from exc
        if len(encoded) > MAX_CONFIG_BYTES:
            raise PersistedStageConfigError("persisted stage config exceeds 1 MiB")
        _exact_keys(
            raw,
            path="config_json",
            required=frozenset({"schema_version", "stages"}),
        )
        if raw["schema_version"] != schema_version:
            raise PersistedStageConfigError(
                "embedded config schema_version differs from GenerationRun"
            )
        stage_values = _plain_object(raw["stages"], path="config_json.stages")
        _exact_keys(
            stage_values,
            path="config_json.stages",
            required=frozenset(self._stage_names),
        )
        bindings: list[ResolvedConfigBinding] = []
        for name in self._stage_names:
            stage_value = _plain_object(
                stage_values[name], path=f"config_json.stages.{name}"
            )
            _exact_keys(
                stage_value,
                path=f"config_json.stages.{name}",
                required=frozenset({"stage_version", "config"}),
            )
            expected_version, config_type = self._types[name]
            if stage_value["stage_version"] != expected_version:
                raise PersistedStageConfigError(
                    f"{name}: unsupported Stage version "
                    f"{stage_value['stage_version']!r} (expected {expected_version})"
                )
            typed = _decode(
                stage_value["config"],
                config_type,
                path=f"config_json.stages.{name}.config",
                depth=0,
            )
            bindings.append(
                ResolvedConfigBinding(
                    stage_name=name,
                    source=source,
                    value=typed,
                )
            )
        return tuple(bindings)

    def json_schema(self) -> dict[str, object]:
        """Versioned, closed JSON Schema suitable for offline manifest checks."""
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:udg:generation:{self._schema_version}",
            "title": "Canonical generation Stage configuration",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "schema_version": {"const": self._schema_version},
                "stages": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        name: {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "stage_version": {"const": self._types[name][0]},
                                "config": _annotation_schema(self._types[name][1]),
                            },
                            "required": ["stage_version", "config"],
                        }
                        for name in self._stage_names
                    },
                    "required": list(self._stage_names),
                },
            },
            "required": ["schema_version", "stages"],
        }
