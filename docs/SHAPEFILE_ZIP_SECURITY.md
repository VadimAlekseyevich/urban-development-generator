# Safe Shapefile ZIP extraction

S03-T03 adds a bounded extraction boundary for untrusted Shapefile ZIP uploads.

`backend.app.services.ShapefileZipExtractor` accepts a seekable binary stream, so workers can
pass the stream returned by `ArtifactStore.open()` without exposing storage paths. Extraction is
temporary and must be consumed inside the context manager:

```python
extractor = ShapefileZipExtractor()

with store.open(artifact_ref) as source:
    with extractor.extract(source) as extracted:
        for shapefile in extracted.shapefiles:
            path = extracted.path_for(shapefile)
            # S03-T04 will inspect vector metadata here.
```

The returned member paths are relative `PurePosixPath` values. The extraction root is an internal
temporary directory and is deleted when the context exits, including when downstream code raises.

## Security policy

The extractor never calls `ZipFile.extract()` or `extractall()`. It validates the complete central
directory before creating extracted files and then copies each regular file in bounded chunks.

Default limits are:

- at most 512 ZIP entries and 256 regular files;
- at most 256 MiB uncompressed per member;
- at most 512 MiB total uncompressed data;
- maximum compression ratio 1000:1;
- path length at most 512 characters and depth at most 16 segments;
- extraction reads use 1 MiB chunks.

`ShapefileZipLimits` is explicit and immutable, so a worker can choose a stricter policy for a
deployment or job type without changing extraction code.

The preflight rejects:

- absolute paths, drive-prefixed paths, `..`, `.`, empty path segments and backslash traversal;
- non-portable Windows reserved names, colon-bearing path segments and trailing dot/space aliases;
- duplicate paths after Unicode normalization and case folding;
- file/directory ancestor conflicts;
- symlink, device, FIFO and other non-regular Unix ZIP entries;
- encrypted members and unsupported compression methods;
- archives that exceed entry/file/member/total-size/compression/path limits;
- archives that contain no `.shp` member.

During extraction, uncompressed byte counts are checked again while streaming and every output path
is asserted to remain below the temporary root. A mismatch between bytes written and ZIP metadata
is rejected.

## Lifecycle boundary

Extraction does not create a `DatasetVersion`, inspect CRS/geometry, normalize vectors or persist
canonical source rows. The uploaded ZIP remains the durable artifact; extracted files are disposable
worker scratch data.

S03-T04 should consume this context-managed result and add metadata-first vector inspection
(layer list, CRS, geometry types, bbox and feature count) without extending extracted-file lifetime.
