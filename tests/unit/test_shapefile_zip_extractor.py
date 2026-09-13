import stat
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

import pytest

from backend.app.services.shapefile_zip import (
    ShapefileZipError,
    ShapefileZipExtractor,
    ShapefileZipLimitError,
    ShapefileZipLimits,
    UnsafeShapefileZipError,
)


def _archive(
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> BytesIO:
    payload = BytesIO()
    with zipfile.ZipFile(payload, mode="w", compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    payload.seek(0)
    return payload


def test_extracts_valid_shapefile_archive_and_cleans_temp_directory(tmp_path: Path) -> None:
    temp_root = tmp_path / "scratch"
    extractor = ShapefileZipExtractor(
        limits=ShapefileZipLimits(chunk_size=3),
        temp_root=temp_root,
    )
    archive = _archive(
        [
            ("city/", b""),
            ("city/roads.shp", b"shape-bytes"),
            ("city/roads.shx", b"index"),
            ("city/roads.dbf", b"attributes"),
            ("city/roads.prj", b"projection"),
        ]
    )

    with extractor.extract(archive) as extracted:
        extraction_root = extracted.root
        assert extracted.files == (
            PurePosixPath("city/roads.dbf"),
            PurePosixPath("city/roads.prj"),
            PurePosixPath("city/roads.shp"),
            PurePosixPath("city/roads.shx"),
        )
        assert extracted.shapefiles == (PurePosixPath("city/roads.shp"),)
        assert extracted.path_for(extracted.shapefiles[0]).read_bytes() == b"shape-bytes"
        assert extraction_root.is_dir()

    assert not extraction_root.exists()
    assert list(temp_root.iterdir()) == []


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape.shp",
        "/absolute.shp",
        "C:/absolute.shp",
        "nested\\..\\escape.shp",
        "nested//roads.shp",
        "nested/./roads.shp",
    ],
)
def test_rejects_zip_slip_and_unsafe_member_paths(
    tmp_path: Path,
    member_name: str,
) -> None:
    temp_root = tmp_path / "scratch"
    extractor = ShapefileZipExtractor(temp_root=temp_root)

    with pytest.raises(UnsafeShapefileZipError):
        with extractor.extract(_archive([(member_name, b"bad")])):
            pytest.fail("unsafe archive unexpectedly extracted")

    assert not (tmp_path / "escape.shp").exists()
    assert list(temp_root.iterdir()) == []


def test_rejects_symlink_entries(tmp_path: Path) -> None:
    payload = BytesIO()
    with zipfile.ZipFile(payload, mode="w") as archive:
        info = zipfile.ZipInfo("roads.shp")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")
    payload.seek(0)

    extractor = ShapefileZipExtractor(temp_root=tmp_path)
    with pytest.raises(UnsafeShapefileZipError, match="non-regular"):
        with extractor.extract(payload):
            pytest.fail("symlink entry unexpectedly extracted")

    assert list(tmp_path.iterdir()) == []


def test_rejects_duplicate_normalized_paths() -> None:
    archive = _archive(
        [
            ("roads.SHP", b"first"),
            ("roads.shp", b"second"),
        ]
    )

    with pytest.raises(UnsafeShapefileZipError, match="duplicate normalized path"):
        with ShapefileZipExtractor().extract(archive):
            pytest.fail("duplicate paths unexpectedly extracted")


def test_enforces_file_member_total_and_compression_limits() -> None:
    with pytest.raises(ShapefileZipLimitError) as file_count:
        with ShapefileZipExtractor(
            limits=ShapefileZipLimits(max_files=2),
        ).extract(
            _archive(
                [
                    ("roads.shp", b"a"),
                    ("roads.shx", b"b"),
                    ("roads.dbf", b"c"),
                ]
            )
        ):
            pytest.fail("file-count limit unexpectedly ignored")
    assert file_count.value.limit_name == "max_files"

    with pytest.raises(ShapefileZipLimitError) as member_size:
        with ShapefileZipExtractor(
            limits=ShapefileZipLimits(
                max_member_uncompressed_bytes=4,
                max_total_uncompressed_bytes=8,
            ),
        ).extract(_archive([("roads.shp", b"12345")])):
            pytest.fail("member-size limit unexpectedly ignored")
    assert member_size.value.limit_name == "max_member_uncompressed_bytes"

    with pytest.raises(ShapefileZipLimitError) as total_size:
        with ShapefileZipExtractor(
            limits=ShapefileZipLimits(
                max_member_uncompressed_bytes=5,
                max_total_uncompressed_bytes=6,
            ),
        ).extract(
            _archive(
                [
                    ("roads.shp", b"1234"),
                    ("roads.dbf", b"5678"),
                ]
            )
        ):
            pytest.fail("total-size limit unexpectedly ignored")
    assert total_size.value.limit_name == "max_total_uncompressed_bytes"

    with pytest.raises(ShapefileZipLimitError) as ratio:
        with ShapefileZipExtractor(
            limits=ShapefileZipLimits(max_compression_ratio=2.0),
        ).extract(_archive([("roads.shp", b"\x00" * 4096)])):
            pytest.fail("compression-ratio limit unexpectedly ignored")
    assert ratio.value.limit_name == "max_compression_ratio"


def test_requires_at_least_one_shp_member_and_does_not_leave_temp_files(
    tmp_path: Path,
) -> None:
    extractor = ShapefileZipExtractor(temp_root=tmp_path)

    with pytest.raises(ShapefileZipError, match=r"\.shp"):
        with extractor.extract(_archive([("readme.txt", b"not a shapefile")])):
            pytest.fail("non-shapefile archive unexpectedly extracted")

    assert list(tmp_path.iterdir()) == []


def test_context_exit_cleans_extraction_when_consumer_fails(tmp_path: Path) -> None:
    extractor = ShapefileZipExtractor(temp_root=tmp_path)
    extraction_root: Path | None = None

    with pytest.raises(RuntimeError, match="consumer failed"):
        with extractor.extract(_archive([("roads.shp", b"data")])) as extracted:
            extraction_root = extracted.root
            assert extraction_root.exists()
            raise RuntimeError("consumer failed")

    assert extraction_root is not None
    assert not extraction_root.exists()
    assert list(tmp_path.iterdir()) == []


def test_limit_configuration_rejects_unbounded_or_inconsistent_values() -> None:
    with pytest.raises(ValueError, match="max_files"):
        ShapefileZipLimits(max_entries=1, max_files=2)

    with pytest.raises(ValueError, match="max_member_uncompressed_bytes"):
        ShapefileZipLimits(
            max_member_uncompressed_bytes=10,
            max_total_uncompressed_bytes=5,
        )

    with pytest.raises(ValueError, match="chunk_size"):
        ShapefileZipLimits(chunk_size=0)


def test_rejects_invalid_zip_without_creating_extraction_directory(tmp_path: Path) -> None:
    extractor = ShapefileZipExtractor(temp_root=tmp_path)

    with pytest.raises(ShapefileZipError, match="invalid ZIP"):
        with extractor.extract(BytesIO(b"not-a-zip")):
            pytest.fail("invalid archive unexpectedly extracted")

    assert list(tmp_path.iterdir()) == []
