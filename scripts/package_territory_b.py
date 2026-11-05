"""SHA-pinned Territory B reference input for a FROM_SCRATCH GIS experiment.

Exports only real OSM-derived polygon constraints plus a metric AOI boundary.
Observed built roads/buildings are audited but *never* inserted as fixed urban
layers: this is a deliberate modeling counterfactual, not an empty city claim.
No download, database call or synthetic data substitution is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Literal

import pyogrio
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from backend.app.services.osm_pbf_reader import (
    OsmFeatureCategory,
    OsmPbfReader,
    _classify_tags,
    _osm_identity,
)
from scripts.package_territory_a import (
    TerritoryPackageError,
    _geometry_family,
    _json_bytes,
    _rows,
    sha256_file,
)

SCHEMA = "territory-b-from-scratch-osm-v1"
ARCHIVED_SOURCE_URL = (
    "https://download.geofabrik.de/russia/central-fed-district-261102.osm.pbf"
)
SOURCE_LAYERS = ("lines", "multipolygons")
CONSTRAINT_KINDS = ("landuse", "water")


class TerritoryBPackageError(ValueError):
    """An input manifest, source or canonical-constraint invariant failed."""


class TerritoryBManifest(BaseModel):
    """Fixed 100 km² Tula-region counterfactual with bounded real OSM evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["territory-b-from-scratch-osm-v1"]
    territory_id: Literal["tula-b"]
    mode: Literal["FROM_SCRATCH"]
    source_url: Literal[
        "https://download.geofabrik.de/russia/central-fed-district-261102.osm.pbf"
    ]
    source_sha256: str
    source_license: Literal["ODbL-1.0"]
    working_srid: Literal[32637]
    center_lon: float
    center_lat: float
    side_m: Literal[10000]
    max_source_features_per_layer: int = Field(ge=1, le=100_000)
    max_output_features_per_kind: int = Field(ge=1, le=100_000)

    @model_validator(mode="after")
    def validate_source_and_aoi(self) -> TerritoryBManifest:
        digest = self.source_sha256
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or set(digest) == {"0"}
        ):
            raise ValueError(
                "source_sha256 must be the lowercase digest of a downloaded, "
                "archived Geofabrik PBF, not a template placeholder"
            )
        if (
            not math.isfinite(self.center_lon)
            or not math.isfinite(self.center_lat)
            or not (36.9 <= self.center_lon <= 38.2)
            or not (53.8 <= self.center_lat <= 54.5)
        ):
            raise ValueError("Territory B center must be within the Tula reference bounds")
        return self


def _square(
    manifest: TerritoryBManifest,
) -> tuple[Polygon, Polygon, tuple[float, float, float, float]]:
    to_metric = Transformer.from_crs(4326, manifest.working_srid, always_xy=True)
    from_metric = Transformer.from_crs(manifest.working_srid, 4326, always_xy=True)
    center_x, center_y = to_metric.transform(
        manifest.center_lon, manifest.center_lat
    )
    half = manifest.side_m / 2
    projected = box(center_x - half, center_y - half, center_x + half, center_y + half)
    geographic = transform(from_metric.transform, projected)
    if not isinstance(geographic, Polygon) or not geographic.is_valid:
        raise TerritoryBPackageError("cannot produce valid EPSG:4326 AOI")
    return projected, geographic, geographic.bounds


def _intersect_metric(
    geometry: object,
    *,
    polygonal: bool,
    metric_square: Polygon,
    to_metric: Transformer,
) -> BaseGeometry | None:
    if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        return None
    candidate = _geometry_family(geometry, polygonal=polygonal)
    if candidate is None:
        return None
    projected = transform(to_metric.transform, candidate)
    return _geometry_family(
        projected.intersection(metric_square), polygonal=polygonal
    )


def prepare_territory_b(
    *,
    manifest: TerritoryBManifest,
    source: Path,
    output_dir: Path,
    require_osm_pbf: bool = True,
) -> dict[str, object]:
    """Validate and atomically promote constraint-only files without DB writes.

    The test-only require_osm_pbf=False seam accepts a synthetic two-layer
    GPKG so GDAL, tag, identity, CRS and clipping code all run in CI.
    """
    if require_osm_pbf and not source.name.endswith(".osm.pbf"):
        raise TerritoryBPackageError("real source must be a local .osm.pbf")
    if not source.is_file() or source.stat().st_size == 0:
        raise TerritoryBPackageError("missing or empty raw source file")
    if output_dir.exists():
        raise TerritoryBPackageError("output directory exists; refusing overwrite")
    source_digest = sha256_file(source)
    if source_digest != manifest.source_sha256:
        raise TerritoryBPackageError("source_sha256 mismatch: archived PBF bytes differ")

    layer_names = {str(layer[0]) for layer in pyogrio.list_layers(source)}
    if not set(SOURCE_LAYERS).issubset(layer_names):
        raise TerritoryBPackageError("source requires OSM lines and multipolygons layers")

    square, geographic_square, geographic_bbox = _square(manifest)
    to_metric = Transformer.from_crs(4326, manifest.working_srid, always_xy=True)
    from_metric = Transformer.from_crs(manifest.working_srid, 4326, always_xy=True)
    reader = OsmPbfReader()
    features: dict[str, dict[str, dict[str, object]]] = {
        kind: {} for kind in CONSTRAINT_KINDS
    }
    excluded_urban: dict[str, set[str]] = {"roads": set(), "buildings": set()}
    row_counts: dict[str, int] = {}

    for layer in SOURCE_LAYERS:
        try:
            rows = _rows(
                source, layer=layer, bbox=geographic_bbox,
                max_features=manifest.max_source_features_per_layer,
            )
        except TerritoryPackageError as exc:
            raise TerritoryBPackageError(str(exc)) from exc
        row_counts[layer] = len(rows)
        for row in rows:
            tags = reader._extract_tags(row)
            categories = _classify_tags(tags)
            if not categories:
                continue
            is_polygonal = layer == "multipolygons"
            clipped = _intersect_metric(
                row.get("geometry"), polygonal=is_polygonal,
                metric_square=square, to_metric=to_metric,
            )
            if clipped is None or not clipped.is_valid or clipped.is_empty:
                continue
            element, osm_id = _osm_identity(row, layer=layer)
            source_id = f"osm:{element.value}:{osm_id}"
            if layer == "lines" and OsmFeatureCategory.ROADS in categories:
                excluded_urban["roads"].add(source_id)
            if layer == "multipolygons" and OsmFeatureCategory.BUILDINGS in categories:
                excluded_urban["buildings"].add(source_id)
            if layer != "multipolygons":
                continue

            for kind, category in (
                ("landuse", OsmFeatureCategory.LANDUSE),
                ("water", OsmFeatureCategory.WATER),
            ):
                if category not in categories:
                    continue
                if source_id in features[kind]:
                    raise TerritoryBPackageError(f"duplicate OSM identity in {kind}")
                features[kind][source_id] = {
                    "type": "Feature",
                    "id": source_id,
                    "geometry": mapping(transform(from_metric.transform, clipped)),
                    "properties": {
                        **tags,
                        "source_feature_id": source_id,
                        "osm_element_type": element.value,
                        "osm_id": osm_id,
                    },
                }
                if len(features[kind]) > manifest.max_output_features_per_kind:
                    raise TerritoryBPackageError(
                        f"{kind} exceeded max_output_features_per_kind"
                    )

    if not any(features[kind] for kind in CONSTRAINT_KINDS):
        raise TerritoryBPackageError(
            "no real polygon constraints within AOI: "
            "refusing to mislabel an empty source as a research dataset"
        )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".territory-b-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        artifacts: dict[str, dict[str, object]] = {}
        for kind in CONSTRAINT_KINDS:
            filename = f"{kind}.geojson"
            collection = {
                "type": "FeatureCollection",
                "features": [
                    features[kind][name] for name in sorted(features[kind])
                ],
            }
            raw = _json_bytes(collection)
            (staging / filename).write_bytes(raw)
            artifacts[kind] = {
                "path": filename,
                "dataset_kind": kind,
                "source_format": "geojson",
                "feature_count": len(collection["features"]),
                "ingest_enabled": bool(collection["features"]),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }

        boundary = _json_bytes({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": mapping(geographic_square),
                "properties": {"territory_id": manifest.territory_id},
            }],
        })
        (staging / "boundary.geojson").write_bytes(boundary)
        report: dict[str, object] = {
            "schema_version": SCHEMA,
            "territory_id": manifest.territory_id,
            "mode": manifest.mode,
            "source": {
                "url": manifest.source_url,
                "sha256": source_digest,
                "license": manifest.source_license,
            },
            "working_crs": f"EPSG:{manifest.working_srid}",
            "projected_area_m2": float(square.area),
            "boundary_wgs84_bbox": list(geographic_bbox),
            "boundary_sha256": hashlib.sha256(boundary).hexdigest(),
            "source_rows_in_bbox": row_counts,
            "fixed_urban_layers_included": [],
            "observed_but_excluded_fixed_features": {
                kind: len(ids) for kind, ids in sorted(excluded_urban.items())
            },
            "derived_artifacts": artifacts,
            "ready_for_ingest_kinds": [
                kind for kind in CONSTRAINT_KINDS if features[kind]
            ],
            "preprocessor": SCHEMA,
            "warning": (
                "FROM_SCRATCH is an intentional no-fixed-urban baseline, "
                "not evidence that the actual Tula-region AOI is undeveloped. "
                "This is source preparation, not a real GIS generation run."
            ),
        }
        (staging / "package-report.json").write_bytes(_json_bytes(report))
        if output_dir.exists():
            raise TerritoryBPackageError(
                "output directory appeared during preparation"
            )
        staging.rename(output_dir)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = TerritoryBManifest.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        if not args.source.is_file() or not args.source.name.endswith(".osm.pbf"):
            raise TerritoryBPackageError("local .osm.pbf source is required")
        if sha256_file(args.source) != manifest.source_sha256:
            raise TerritoryBPackageError("source_sha256 mismatch")
        if args.verify_only:
            print(json.dumps({
                "verified": True,
                "source_sha256": manifest.source_sha256,
                "territory_id": manifest.territory_id,
            }, sort_keys=True))
            return
        report = prepare_territory_b(
            manifest=manifest, source=args.source, output_dir=args.output_dir
        )
    except (OSError, ValidationError, TerritoryBPackageError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
