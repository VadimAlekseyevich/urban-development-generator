from __future__ import annotations

from dataclasses import dataclass

from core.urban_generator.domain.territory import SnapshotLayerKind, SnapshotLayerRef


class FixedExistingZonesAdapterError(ValueError):
    """Raised when existing source zoning cannot be adapted safely."""


@dataclass(frozen=True, slots=True)
class FixedExistingZonesAdapter:
    """Adapt selected fixed LANDUSE layer refs into semantic fixed ZONES refs.

    The adapter is intentionally infrastructure-independent. It does not read or mutate
    source features and it does not infer a functional class from arbitrary landuse values.
    The caller is responsible for selecting source layers that already represent existing
    functional zoning in canonical source storage.
    """

    def adapt(
        self,
        source_layers: tuple[SnapshotLayerRef, ...],
    ) -> tuple[SnapshotLayerRef, ...]:
        """Return canonical fixed-zone refs without modifying the input layer refs."""

        if not isinstance(source_layers, tuple):
            raise FixedExistingZonesAdapterError(
                "source_layers must be an immutable tuple of SnapshotLayerRef values"
            )

        source_refs: list[str] = []
        for layer in source_layers:
            if not isinstance(layer, SnapshotLayerRef):
                raise FixedExistingZonesAdapterError(
                    "source_layers must contain only SnapshotLayerRef values"
                )
            if layer.kind is not SnapshotLayerKind.LANDUSE:
                raise FixedExistingZonesAdapterError(
                    "fixed existing zones must be adapted from LANDUSE source layers"
                )
            source_refs.append(layer.source_ref)

        if len(source_refs) != len(set(source_refs)):
            raise FixedExistingZonesAdapterError(
                "fixed existing zone source refs must be unique"
            )

        return tuple(
            SnapshotLayerRef(kind=SnapshotLayerKind.ZONES, source_ref=source_ref)
            for source_ref in sorted(source_refs)
        )
