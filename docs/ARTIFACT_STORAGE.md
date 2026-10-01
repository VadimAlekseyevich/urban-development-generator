# Artifact storage adapters

`ArtifactStore` в `core.urban_generator.domain` является storage-agnostic port: domain/application code работает с `ArtifactRef` и `ArtifactStat`, а не с filesystem paths или vendor-specific URIs.

## LocalArtifactStore

`backend.app.adapters.LocalArtifactStore` — локальный filesystem adapter для development и single-host deployment. Корень передаётся явно и в runtime должен браться из `Settings.storage_root` / `STORAGE_ROOT`.

Внутренний layout не является public contract:

```text
<storage_root>/
  temporary/<logical-key>
  ready/<logical-key>
  .metadata/temporary/<logical-key>.json
  .metadata/ready/<logical-key>.json
```

Пользовательский `ArtifactRef.key` остаётся логическим ключом. Adapter повторно проверяет, что вычисленный путь не выходит за configured root, и отклоняет symlink/path escapes.

### Write contract

`put()` принимает только `temporary` refs. Source читается bounded chunks (по умолчанию 1 MiB), поэтому adapter не требует загрузки всего blob в RAM. Payload и metadata сначала пишутся во временные sibling files, `fsync`-ятся и публикуются через `os.replace`. Повторный `put()` может заменить только temporary blob; существующий ready blob никогда не перезаписывается.

Metadata sidecar хранит SHA-256, размер и optional content type. `stat()` проверяет, что фактический размер payload совпадает с metadata. Sidecar нужен, чтобы metadata переживала перезапуск процесса без зависимости от PostgreSQL.

### Promotion and retries

`promote()` переводит blob из temporary namespace в ready namespace. Payload и metadata перемещаются отдельными атомарными `os.replace`, поэтому весь двухфайловый переход не является одной filesystem transaction. Adapter распознаёт промежуточное состояние и завершает прерванный promote при следующем вызове. После успешного promote повторные вызовы с temporary или ready ref возвращают тот же ready `ArtifactStat`.

`delete()` idempotent и удаляет payload вместе с sidecar.

## Persistence boundary

Filesystem adapter отвечает только за bytes и их storage metadata. Таблица `artifacts` остаётся authoritative persistence lifecycle (`temporary/ready/referenced/expired`) и связывает URI/checksum/owner с DB entities. LocalArtifactStore сам не создаёт и не изменяет ORM rows: координация blob write и DB metadata относится к application/ingest use case.

## Streaming upload API

`POST /api/v1/uploads` принимает один multipart `file`. Browser-provided filename считается недоверенным: path components удаляются, Unicode нормализуется, небезопасные символы заменяются, имя ограничено 180 символами. Storage key имеет вид `uploads/<random-id>/<sanitized-name>` и не раскрывает filesystem path.

Лимит файла задаётся `MAX_UPLOAD_SIZE_MB` и переводится в MiB (`1024 * 1024`). `UploadFile.size`, когда он доступен, используется для раннего отказа до копирования в ArtifactStore; фактический поток дополнительно оборачивается bounded reader, поэтому отсутствие/ошибка size hint не позволяет обойти лимит. `LocalArtifactStore` читает этот поток bounded chunks и одновременно вычисляет SHA-256 — полного `read()` всего файла в RAM нет.

FastAPI/Starlette multipart parser может использовать `SpooledTemporaryFile`: небольшой multipart file может находиться в памяти, а более крупный spill-ится во временный файл до входа в endpoint. Application layer затем выполняет bounded copy в ArtifactStore; он никогда не материализует полный payload как `bytes`.

Успешный upload сначала записывается как temporary artifact, затем promote-ится в `ready`. После этого создаётся DB row `artifacts` со storage-neutral URI `artifact://<logical-key>`, checksum, size, content type и state `ready`. Если DB persistence завершается ошибкой, application service best-effort удаляет temporary/ready blob, чтобы обычная ошибка транзакции не оставляла orphan.

Ответ содержит `artifact_id`, logical `key`, sanitized `filename`, state, размер, checksum и content type. DatasetVersion/format-specific inspection здесь не создаются: это отдельные последующие ingest work items.

### Known boundary

Blob storage и PostgreSQL не образуют общей distributed transaction. Компенсирующее удаление покрывает штатную DB failure path, но авария процесса между filesystem promote и DB commit всё ещё может оставить orphan; lifecycle cleanup/orphan reconciliation должен обнаруживать такие объекты отдельно.


## S12 stage artifact publication and orphan reconciliation

S12-T08 uses the existing `ArtifactStore` and `Artifact` lifecycle, not an alternate
blob or provenance model; see [ADR-0005](adr/0005-stage-artifact-publication-gc.md).
Stage producers write their own unique logical
`runs/<run UUID>/stages/<stage name>/<artifact name>` key with
`ArtifactStore.put(temporary_ref, source)`, then call
`SqlAlchemyStageArtifactPublisher.publish(run_id, stage_name, temporary_stat)`
while the run and stage are **running**. The publisher checks content identity,
records an unowned temporary DB row, promotes the blob, and commits both
`temporary -> ready -> referenced` DB transitions and the relational
`run_stage_result_artifacts` link in one transaction. The intermediate ready
state is flushed to satisfy the existing DB lifecycle trigger, but is not
committed separately. An exact duplicate call is idempotent; wrong provenance
or terminal stage/run is rejected. Blob promotion is separately retryable and
resumes an interrupted payload/metadata move; failure leaves an unowned
temporary DB row until retry or aged cleanup. The returned `PublishedStageArtifact`
contains only stable ID/stage-result ID and ready `ArtifactStat`.

`worker.tasks.gc_orphan_artifacts` is registered hourly via ARQ and uses
`SqlAlchemyArtifactGc` with default two-hour age floor, 50 DB-row/physical-key
batch and 5,000 directory-entry local scan budget. PostgreSQL row locks and
`SKIP LOCKED` protect authoritative state from conflicting publication. GC
removes only stale, unowned, unlinked run-stage temporary/ready rows and local
blob/metadata residue (including half-promoted and unregistered objects).
`LocalArtifactStore.stale_run_refs()` scans only `runs/` without following
symlinks, preserves a process-local scan cursor between hourly worker runs to avoid
reexamining the same referenced prefix indefinitely, and rechecks all storage
namespaces before deletion. Already referenced
objects, recent writes and uploads are never collected. Missing physical blobs
may still expire abandoned temporary DB metadata. Since storage and DB do not
share a transaction, GC failures remain retryable and the next bounded pass
can reconcile crash residue. This scanner is local-adapter-specific and does not
expand the storage-neutral core port.

This does not automatically infer generated artifacts from `StageResult` or
retrofit existing upload/ingest code. The publishing stage or its typed
application adapter must explicitly invoke the publisher before completing
its stage; further backend/store adapter parity remains future work.


## S13 asynchronous export artifacts

S13-T07 uses the same `ArtifactStore` and DB `Artifact` lifecycle for
single-layer GeoJSON exports. HTTP creation persists `Job + GeoJsonExport +
JobOutbox` only; the worker writes `exports/{project}/{job}/...` bytes,
promotes them to ready, then references the artifact with `owner_type=job`
while marking the export job succeeded. Export bytes are streamed from a
spooled temporary file into the store and download uses `ArtifactStore.open()`,
so neither API nor application contracts expose filesystem paths.

The export key is deterministic for one immutable request. If the worker dies
after storage promotion but before DB completion, retry compares the regenerated
checksum/size/content type against the ready blob and reuses it. Storage and DB
still lack a distributed transaction; generalized cleanup of export-only
orphans remains part of the later bounded artifact-GC hardening.
