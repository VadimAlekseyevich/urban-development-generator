import re
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Final

_DEFAULT_CHUNK_SIZE: Final = 1024 * 1024
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_SUPPORTED_COMPRESSION = frozenset(
    {
        zipfile.ZIP_STORED,
        zipfile.ZIP_DEFLATED,
        zipfile.ZIP_BZIP2,
        zipfile.ZIP_LZMA,
    }
)


class ShapefileZipError(ValueError):
    """Base error for invalid or unsafe Shapefile ZIP archives."""


class UnsafeShapefileZipError(ShapefileZipError):
    """Raised when an archive contains an unsafe path or entry type."""


class ShapefileZipLimitError(ShapefileZipError):
    """Raised when a configured archive extraction limit is exceeded."""

    def __init__(self, *, limit_name: str, limit: int | float, observed: int | float) -> None:
        self.limit_name = limit_name
        self.limit = limit
        self.observed = observed
        super().__init__(f"{limit_name} limit exceeded: {observed} > {limit}")


@dataclass(frozen=True, slots=True)
class ShapefileZipLimits:
    """Bounded extraction policy for untrusted Shapefile ZIP archives."""

    max_entries: int = 512
    max_files: int = 256
    max_member_uncompressed_bytes: int = 256 * 1024 * 1024
    max_total_uncompressed_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 1000.0
    max_path_length: int = 512
    max_path_depth: int = 16
    chunk_size: int = _DEFAULT_CHUNK_SIZE

    def __post_init__(self) -> None:
        integer_limits = {
            "max_entries": self.max_entries,
            "max_files": self.max_files,
            "max_member_uncompressed_bytes": self.max_member_uncompressed_bytes,
            "max_total_uncompressed_bytes": self.max_total_uncompressed_bytes,
            "max_path_length": self.max_path_length,
            "max_path_depth": self.max_path_depth,
            "chunk_size": self.chunk_size,
        }
        for name, value in integer_limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.max_compression_ratio, bool)
            or not isinstance(self.max_compression_ratio, int | float)
            or self.max_compression_ratio <= 0
        ):
            raise ValueError("max_compression_ratio must be positive")
        if self.max_files > self.max_entries:
            raise ValueError("max_files must not exceed max_entries")
        if self.max_member_uncompressed_bytes > self.max_total_uncompressed_bytes:
            raise ValueError(
                "max_member_uncompressed_bytes must not exceed max_total_uncompressed_bytes"
            )


@dataclass(frozen=True, slots=True)
class ExtractedShapefileArchive:
    """Temporary extraction result. Relative paths are valid only inside extract()."""

    root: Path
    files: tuple[PurePosixPath, ...]
    shapefiles: tuple[PurePosixPath, ...]

    def path_for(self, relative_path: PurePosixPath) -> Path:
        if relative_path not in self.files:
            raise KeyError(relative_path)
        return self.root.joinpath(*relative_path.parts)


@dataclass(frozen=True, slots=True)
class _MemberPlan:
    info: zipfile.ZipInfo
    relative_path: PurePosixPath
    is_directory: bool


class ShapefileZipExtractor:
    """Safely extract an untrusted Shapefile ZIP into a temporary directory."""

    def __init__(
        self,
        *,
        limits: ShapefileZipLimits | None = None,
        temp_root: str | Path | None = None,
    ) -> None:
        self._limits = limits or ShapefileZipLimits()
        self._temp_root = Path(temp_root).expanduser().resolve() if temp_root is not None else None
        if self._temp_root is not None:
            self._temp_root.mkdir(parents=True, exist_ok=True)
            if not self._temp_root.is_dir():
                raise ValueError("temp_root must be a directory")

    @contextmanager
    def extract(self, source: BinaryIO) -> Iterator[ExtractedShapefileArchive]:
        """Validate and extract one archive, deleting all extracted files on context exit."""

        try:
            archive = zipfile.ZipFile(source, mode="r")
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ShapefileZipError("invalid ZIP archive") from exc

        with archive:
            plans = self._build_plan(archive)
            with tempfile.TemporaryDirectory(
                prefix="urban-shapefile-",
                dir=self._temp_root,
            ) as directory:
                root = Path(directory).resolve()
                try:
                    files = self._extract_plans(archive, plans, root)
                except (zipfile.BadZipFile, EOFError) as exc:
                    raise ShapefileZipError("ZIP archive is corrupt during extraction") from exc

                shapefiles = tuple(path for path in files if path.suffix.casefold() == ".shp")

                yield ExtractedShapefileArchive(
                    root=root,
                    files=files,
                    shapefiles=shapefiles,
                )

    def _build_plan(self, archive: zipfile.ZipFile) -> tuple[_MemberPlan, ...]:
        infos = archive.infolist()
        if len(infos) > self._limits.max_entries:
            raise ShapefileZipLimitError(
                limit_name="max_entries",
                limit=self._limits.max_entries,
                observed=len(infos),
            )

        plans: list[_MemberPlan] = []
        file_count = 0
        total_uncompressed = 0
        seen_paths: dict[tuple[str, ...], str] = {}
        files: set[tuple[str, ...]] = set()
        directories: set[tuple[str, ...]] = set()

        for info in infos:
            is_directory = info.is_dir()
            relative = self._safe_relative_path(info.filename, is_directory=is_directory)
            canonical = tuple(part.casefold() for part in relative.parts)

            previous = seen_paths.get(canonical)
            if previous is not None:
                raise UnsafeShapefileZipError(
                    "archive contains duplicate normalized path: "
                    f"{previous!r} and {info.filename!r}"
                )
            seen_paths[canonical] = info.filename

            self._validate_entry_type(info, is_directory=is_directory)
            self._validate_path_conflicts(canonical, is_directory, files, directories)
            for depth in range(1, len(canonical)):
                directories.add(canonical[:depth])

            if is_directory:
                directories.add(canonical)
            else:
                file_count += 1
                if file_count > self._limits.max_files:
                    raise ShapefileZipLimitError(
                        limit_name="max_files",
                        limit=self._limits.max_files,
                        observed=file_count,
                    )
                if info.flag_bits & 0x1:
                    raise UnsafeShapefileZipError(
                        f"encrypted ZIP entries are not supported: {info.filename}"
                    )
                if info.compress_type not in _SUPPORTED_COMPRESSION:
                    raise UnsafeShapefileZipError(
                        f"unsupported ZIP compression method for {info.filename}"
                    )
                if info.file_size > self._limits.max_member_uncompressed_bytes:
                    raise ShapefileZipLimitError(
                        limit_name="max_member_uncompressed_bytes",
                        limit=self._limits.max_member_uncompressed_bytes,
                        observed=info.file_size,
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > self._limits.max_total_uncompressed_bytes:
                    raise ShapefileZipLimitError(
                        limit_name="max_total_uncompressed_bytes",
                        limit=self._limits.max_total_uncompressed_bytes,
                        observed=total_uncompressed,
                    )

                ratio = self._compression_ratio(info)
                if ratio > self._limits.max_compression_ratio:
                    raise ShapefileZipLimitError(
                        limit_name="max_compression_ratio",
                        limit=self._limits.max_compression_ratio,
                        observed=ratio,
                    )
                files.add(canonical)

            plans.append(
                _MemberPlan(
                    info=info,
                    relative_path=relative,
                    is_directory=is_directory,
                )
            )

        if not any(
            not plan.is_directory and plan.relative_path.suffix.casefold() == ".shp"
            for plan in plans
        ):
            raise ShapefileZipError("archive does not contain a .shp file")

        plans.sort(key=lambda plan: plan.relative_path.as_posix().casefold())
        return tuple(plans)

    def _safe_relative_path(self, raw_name: str, *, is_directory: bool) -> PurePosixPath:
        if not raw_name or "\x00" in raw_name:
            raise UnsafeShapefileZipError(
                "archive member name must be non-empty and contain no NUL"
            )
        normalized = unicodedata.normalize("NFKC", raw_name).replace("\\", "/")
        if is_directory:
            normalized = normalized.rstrip("/")
        if not normalized:
            raise UnsafeShapefileZipError("archive member path is empty")
        if len(normalized) > self._limits.max_path_length:
            raise ShapefileZipLimitError(
                limit_name="max_path_length",
                limit=self._limits.max_path_length,
                observed=len(normalized),
            )
        if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized):
            raise UnsafeShapefileZipError(f"absolute archive path is forbidden: {raw_name!r}")

        parts = normalized.split("/")
        if len(parts) > self._limits.max_path_depth:
            raise ShapefileZipLimitError(
                limit_name="max_path_depth",
                limit=self._limits.max_path_depth,
                observed=len(parts),
            )
        for part in parts:
            if not part or part in {".", ".."}:
                raise UnsafeShapefileZipError(
                    f"archive path contains empty, dot or parent segment: {raw_name!r}"
                )
            if ":" in part or part.endswith((" ", ".")):
                raise UnsafeShapefileZipError(
                    f"archive path is not portable/safe: {raw_name!r}"
                )
            stem = part.split(".", 1)[0].upper()
            if stem in _RESERVED_WINDOWS_NAMES:
                raise UnsafeShapefileZipError(
                    f"archive path uses a reserved filesystem name: {raw_name!r}"
                )

        return PurePosixPath(*parts)

    @staticmethod
    def _validate_entry_type(info: zipfile.ZipInfo, *, is_directory: bool) -> None:
        if info.create_system != 3:
            return
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind == 0:
            return
        if is_directory:
            if kind != stat.S_IFDIR:
                raise UnsafeShapefileZipError(
                    f"directory entry has unsafe filesystem type: {info.filename}"
                )
            return
        if kind != stat.S_IFREG:
            raise UnsafeShapefileZipError(
                f"non-regular ZIP entry is forbidden: {info.filename}"
            )

    @staticmethod
    def _validate_path_conflicts(
        canonical: tuple[str, ...],
        is_directory: bool,
        files: set[tuple[str, ...]],
        directories: set[tuple[str, ...]],
    ) -> None:
        for depth in range(1, len(canonical)):
            parent = canonical[:depth]
            if parent in files:
                raise UnsafeShapefileZipError(
                    "archive path would place content below a regular file"
                )
        if is_directory:
            if canonical in files:
                raise UnsafeShapefileZipError(
                    "archive path is both a regular file and a directory"
                )
        elif canonical in directories:
            raise UnsafeShapefileZipError(
                "archive path is both a directory and a regular file"
            )

    @staticmethod
    def _compression_ratio(info: zipfile.ZipInfo) -> float:
        if info.file_size == 0:
            return 0.0
        if info.compress_size <= 0:
            return float("inf")
        return info.file_size / info.compress_size

    def _extract_plans(
        self,
        archive: zipfile.ZipFile,
        plans: tuple[_MemberPlan, ...],
        root: Path,
    ) -> tuple[PurePosixPath, ...]:
        extracted: list[PurePosixPath] = []
        total_written = 0

        for plan in plans:
            target = root.joinpath(*plan.relative_path.parts)
            self._assert_within_root(root, target)
            if plan.is_directory:
                target.mkdir(parents=True, exist_ok=False)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            member_written = 0
            with archive.open(plan.info, mode="r") as source, target.open("xb") as destination:
                while True:
                    member_remaining = (
                        self._limits.max_member_uncompressed_bytes - member_written + 1
                    )
                    total_remaining = (
                        self._limits.max_total_uncompressed_bytes - total_written + 1
                    )
                    read_size = min(
                        self._limits.chunk_size,
                        member_remaining,
                        total_remaining,
                    )
                    chunk = source.read(read_size)
                    if not chunk:
                        break

                    member_written += len(chunk)
                    total_written += len(chunk)
                    if member_written > self._limits.max_member_uncompressed_bytes:
                        raise ShapefileZipLimitError(
                            limit_name="max_member_uncompressed_bytes",
                            limit=self._limits.max_member_uncompressed_bytes,
                            observed=member_written,
                        )
                    if total_written > self._limits.max_total_uncompressed_bytes:
                        raise ShapefileZipLimitError(
                            limit_name="max_total_uncompressed_bytes",
                            limit=self._limits.max_total_uncompressed_bytes,
                            observed=total_written,
                        )
                    destination.write(chunk)

            if member_written != plan.info.file_size:
                raise UnsafeShapefileZipError(
                    f"ZIP member size does not match metadata: {plan.info.filename}"
                )
            extracted.append(plan.relative_path)

        return tuple(extracted)

    @staticmethod
    def _assert_within_root(root: Path, target: Path) -> None:
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise UnsafeShapefileZipError("archive member escapes extraction root")
