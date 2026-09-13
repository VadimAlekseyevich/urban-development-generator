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
