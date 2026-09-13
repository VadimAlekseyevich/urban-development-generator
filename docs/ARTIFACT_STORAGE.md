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

S03-T02 должен использовать этот adapter для streaming upload: HTTP body передаётся в temporary artifact без full-file RAM buffering, а DB metadata создаётся/обновляется только в согласованном application flow.
