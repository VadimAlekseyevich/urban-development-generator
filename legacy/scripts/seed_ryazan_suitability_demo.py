"""Seed a synthetic Ryazan-positioned suitability artifact for the S04-T12 UI demo.

This is demo tooling only. It does not represent a suitability analysis of Ryazan and
must not be used as an input to production decisions.
"""

import uuid

import numpy as np
from pyproj import Transformer
from sqlalchemy.orm import Session

from backend.app.adapters import LocalArtifactStore
from backend.app.core.config import settings
from backend.app.db.session import engine
from backend.app.models.artifact import Artifact, ArtifactLifecycleState
from core.urban_generator.domain import ArtifactRef
from core.urban_generator.suitability.aggregation import WeightedSuitabilityResult
from core.urban_generator.suitability.artifact import SuitabilityArtifactWriter
from core.urban_generator.suitability.config import (
    SuitabilityConfig,
    SuitabilityFactorConfig,
    SuitabilityNormalization,
    SuitabilityThresholds,
)
from core.urban_generator.suitability.factors import SuitabilityGridSpec
from core.urban_generator.suitability.hard_exclusion import HardExclusionMask

_WORKING_SRID = 32637
_WIDTH = 256
_HEIGHT = 192


def _grid() -> SuitabilityGridSpec:
    transformer = Transformer.from_crs(4326, _WORKING_SRID, always_xy=True)
    west, south = transformer.transform(39.55, 54.50)
    east, north = transformer.transform(39.95, 54.75)
    return SuitabilityGridSpec(
        working_srid=_WORKING_SRID,
        bounds=(min(west, east), min(south, north), max(west, east), max(south, north)),
        width=_WIDTH,
        height=_HEIGHT,
    )


def _synthetic_result(
    grid: SuitabilityGridSpec,
) -> tuple[SuitabilityConfig, WeightedSuitabilityResult, HardExclusionMask]:
    rows, columns = np.indices(grid.shape, dtype=np.float64)
    x = columns / max(1, grid.width - 1)
    y = rows / max(1, grid.height - 1)

    radial = 1.0 - np.sqrt(((x - 0.56) / 0.72) ** 2 + ((y - 0.46) / 0.78) ** 2)
    texture = 0.10 * np.sin(x * 8.0 * np.pi) * np.cos(y * 5.0 * np.pi)
    scores = np.clip(0.18 + 0.78 * radial + texture, 0.0, 1.0)

    hard = np.zeros(grid.shape, dtype=np.bool_)
    hard[:4, :] = True
    hard[-4:, :] = True
    hard[:, :4] = True
    hard[:, -4:] = True
    river_center = 0.43 + 0.05 * np.sin(y * 4.0 * np.pi)
    hard |= np.abs(x - river_center) < 0.018

    valid = ~hard
    missing_patch = (x > 0.78) & (x < 0.86) & (y > 0.14) & (y < 0.23)
    valid[missing_patch] = False
    scores[~valid] = 0.0

    config = SuitabilityConfig(
        version="ryazan-synthetic-demo-v1",
        factors=(
            SuitabilityFactorConfig(
                code="demo_pattern",
                weight=1.0,
                normalization=SuitabilityNormalization.IDENTITY,
            ),
        ),
        thresholds=SuitabilityThresholds(
            minimum_score=0.40,
            preferred_score=0.72,
        ),
    )
    result = WeightedSuitabilityResult(
        grid=grid,
        scores=scores,
        valid_mask=valid,
        hard_excluded_mask=hard,
        config_version=config.version,
        config_fingerprint=config.fingerprint,
        factor_versions=(("demo_pattern", "synthetic-v1"),),
        diagnostics=("demo_only=true", "city_reference=Ryazan"),
    )
    hard_mask = HardExclusionMask(
        grid=grid,
        excluded=hard,
        source_codes=("boundary", "demo_water"),
    )
    return config, result, hard_mask


def main() -> None:
    grid = _grid()
    config, result, hard_mask = _synthetic_result(grid)
    key = f"demos/ryazan/suitability/{uuid.uuid4().hex}.tif"
    store = LocalArtifactStore(settings.storage_root)
    output = SuitabilityArtifactWriter(tile_size=128).write(
        store,
        output_ref=ArtifactRef(key=key),
        result=result,
        config=config,
        hard_mask=hard_mask,
    )

    with Session(engine, expire_on_commit=False) as session:
        artifact = Artifact(
            uri=f"artifact://{output.stat.ref.key}",
            checksum=output.stat.checksum,
            size_bytes=output.stat.size_bytes,
            content_type=output.stat.content_type,
            state=ArtifactLifecycleState.READY.value,
            owner_type=None,
            owner_id=None,
        )
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id

    print("Synthetic demo only; this is not a real suitability analysis of Ryazan.")
    print(f"Suitability Artifact ID: {artifact_id}")
    print(f"Open: http://localhost:5173/?suitability_artifact_id={artifact_id}")


if __name__ == "__main__":
    main()
