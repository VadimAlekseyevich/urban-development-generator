# Модель данных

Система разделяет логические datasets, их неизменяемые версии, нормализованные пространственные объекты и результаты конкретных запусков генерации. Каждый запуск воспроизводим через `seed`, нормализованную JSON-конфигурацию, её schema version, рабочую CRS, конкретные версии входных наборов и commit SHA приложения.

## Project

Контейнер исследования: название, описание, граница территории, metadata границы и явный `working_srid`. Метрические операции не выполняются без валидной проектной CRS.

## Dataset

Логический набор данных внутри проекта. Он хранит устойчивую идентичность набора (`project_id`, `kind`) и не содержит upload-specific полей, статуса файла или filesystem path.

## DatasetVersion

Конкретная версия источника для `Dataset`: порядковый `version`, SHA-256 checksum, lifecycle `status`, `source_metadata` и время создания. Идентичность и содержимое версии не редактируются in-place; при изменении входных данных создаётся новая версия. Lifecycle `status` может обновляться отдельно.

Legacy datasets из ранней схемы мигрируют в `version = 1`. Если исторический checksum неизвестен, он остаётся `NULL`, а не вычисляется или подставляется фиктивно. Старый `storage_path` сохраняется только как legacy metadata при миграции; новая модель не имеет first-class filesystem path. Физические blobs описываются отдельными `Artifact` records и доступны прикладному слою через `ArtifactStore`.

## GenerationRun

Воспроизводимый запуск хранит `project_id`, lifecycle `status`, `mode`, `seed`, snapshot `working_srid`, нормализованный `config_json`, `config_schema_version`, commit SHA приложения, timestamps, метрики и диагностику ошибок. Входные данные фиксируются ссылками на конкретные `DatasetVersion` через `generation_run_dataset_versions`, а не на изменяемый логический `Dataset`.

`mode` ограничен значениями `EXPANSION` и `FROM_SCRATCH`. Успешный run (`status = succeeded`) должен иметь commit SHA. После перехода в успешное состояние scalar-поля run и набор ссылок на `DatasetVersion` неизменяемы; защита существует как в ORM, так и на уровне PostgreSQL trigger. Добавить или удалить dataset-version ref успешного запуска нельзя. DatasetVersion, уже используемую запуском, нельзя удалить через FK `RESTRICT`.

Миграция legacy `GenerationRun` присваивает историческим строкам `mode = EXPANSION`, `config_schema_version = legacy-v0` и копирует `working_srid` из проекта. Старое `code_version` переносится в `commit_sha` только если уже является 40-символьным hex commit SHA; неизвестная или произвольная версия не подменяется фиктивным SHA.

## RunStageResult

Каждая алгоритмическая стадия конкретного запуска имеет одну запись `RunStageResult`, уникальную по `(run_id, stage_name)`. Запись фиксирует `stage_version`, lifecycle `status`, `progress_percent`, SHA-256 hash входов и stage-конфигурации, timestamps, структурированные diagnostics и ссылки на созданные stage artifacts.

`input_hash` и `config_hash` хранятся в каноническом формате `sha256:<64 lowercase hex>`, совместимом с соглашением `StageFingerprint` в core. `progress_percent` ограничен диапазоном 0–100, а успешная стадия (`status = succeeded`) обязана иметь прогресс 100. Поддерживаются состояния `pending`, `running`, `succeeded`, `failed`, `cancelled` и `skipped`.

Новые stage artifact refs хранятся реляционно через `run_stage_result_artifacts` и ссылаются на полноценные `Artifact` records. Поле `artifact_refs_json` сохранено только для provenance строк, созданных до S02-T05: исторические opaque refs не конвертируются автоматически, потому что у них нет достоверных URI/hash/size metadata.

Stage results являются частью provenance завершённого run. PostgreSQL trigger запрещает вставку, изменение и удаление `RunStageResult`, если родительский `GenerationRun` уже имеет `status = succeeded`. Такой же guard применяется к `run_stage_result_artifacts`, поэтому artifact provenance успешного run также неизменяем.

## Artifact

`Artifact` хранит metadata физического blob без переноса storage concerns в `core`: `uri`, канонический SHA-256 `checksum`, `size_bytes`, optional `content_type`, lifecycle `state` и optional owner identity (`owner_type`, `owner_id`). URI является persistence/storage metadata; алгоритмическое ядро продолжает работать с opaque `ArtifactRef` и `ArtifactStore`.

Lifecycle состоит из состояний `temporary`, `ready`, `referenced`, `expired`. Нормальный путь публикации — `temporary -> ready -> referenced`; cleanup допускает `temporary -> expired`, `ready -> expired` и `referenced -> expired`. `expired` terminal. После `ready` URI/hash/size/type неизменяемы. Owner назначается только при `ready -> referenced` и после этого не меняется.

`temporary` и `ready` не имеют owner; `referenced` обязан иметь `owner_type` и `owner_id`. Для `expired` owner сохраняется, если artifact ранее был referenced, чтобы cleanup не разрушал provenance. PostgreSQL constraints и lifecycle trigger дублируют критичные ORM guards.

## Job

`Job` — authoritative запись состояния фоновой задачи в PostgreSQL. Она хранит `project_id`, optional `run_id`, стабильный `job_type`, `idempotency_key`, lifecycle `status`, число выполненных `attempt_count`, предел `max_attempts`, timestamps и структурированную информацию об ошибке.

Повторная постановка логически той же работы не создаёт вторую запись: уникальность обеспечивается по `(project_id, job_type, idempotency_key)`. Счётчик попыток не может быть отрицательным или превышать `max_attempts`; `max_attempts` всегда положителен. Поддерживаются состояния `queued`, `running`, `succeeded`, `failed`, `cancelled`.

Ошибки сохраняются без разбора текста исключения: `error_class` соответствует стабильной core taxonomy (`domain`, `config`, `data`, `transient`, `permanent`, `cancelled`), `error_code` хранит машинный code, а `error_json` — message/details и признаки `retryable`/`cancelled`.

## JobOutbox

`JobOutbox` — DB-authoritative состояние доставки `Job` из PostgreSQL в Redis/ARQ. Для каждой job существует не более одной outbox-записи (`job_id` unique). Она хранит `queue_name`, JSON payload, состояние `pending/dispatched`, число delivery attempts, время последней попытки, `next_attempt_at`, последнюю ошибку и `dispatched_at`.

Dispatcher выбирает только due `pending` rows ограниченными batch-ами (не более 500) и использует `FOR UPDATE SKIP LOCKED`, чтобы несколько dispatcher processes не забирали один и тот же DB row одновременно. После ошибки Redis запись остаётся `pending` и получает будущий `next_attempt_at`; после подтверждённого enqueue она становится `dispatched`.

Доставка имеет семантику at-least-once. Queue identity детерминирована как `job:<job_id>`. Если Redis принял сообщение, но процесс упал до commit состояния `dispatched`, следующий проход может повторить enqueue с тем же identity. PostgreSQL остаётся source of truth; Redis не определяет lifecycle `Job`. Это сознательно устраняет окно потери работы между независимыми DB и Redis транзакциями.

S02-T07 фиксирует persistence и dispatcher contract, но не привязывает его к конкретному ARQ client lifecycle. Реальный Redis adapter может реализовать `RedisJobEnqueuer`, используя deterministic queue identity, не меняя DB-модель.

## Generated entities

Результаты генерации не смешиваются с source layers и хранятся в отдельных run-scoped таблицах: `generated_zones`, `generated_roads`, `generated_blocks`, `generated_parcels`, `generated_buildings` и `generated_infrastructure`.

Каждая строка имеет UUID `id`, обязательный `run_id`, пространственную `geometry`, JSON-объект `attributes_json` и `created_at`. `run_id` ссылается на конкретный `GenerationRun` и не может быть переназначен другой генерации.

Геометрии хранятся в рабочей CRS конкретного run: PostgreSQL trigger сверяет `ST_SRID(geometry)` со snapshot `GenerationRun.working_srid`. Zone хранится как `MULTIPOLYGON`, Road как `LINESTRING`, Block/Parcel/Building как `POLYGON`; Infrastructure использует общий `GEOMETRY`, поскольку инфраструктурный объект может быть точечным или площадным.

Generated rows можно создавать и изменять только пока run не находится в состоянии `succeeded`. После успеха trigger запрещает `INSERT`, `UPDATE` и `DELETE`, поэтому пространственный результат завершённого запуска остаётся неизменяемой частью provenance.

S02-T10 добавляет каждому generated layer составной B-tree `(run_id, id)` для run-scoped/keyset access и GiST по `geometry` для bbox/spatial predicates.

## Canonical source layers

Нормализованные исходные векторные данные не складываются в одну EAV/feature-таблицу. S02-T09 вводит отдельные таблицы `source_roads`, `source_buildings`, `source_landuse`, `source_water`, `source_facilities` и `source_constraints`.

Каждая source row принадлежит ровно одному `DatasetVersion` через `dataset_version_id`, может сохранять внешний `source_feature_id`, содержит рабочую `geometry`, JSON-объект `attributes_json` для неканонических provenance/tags и `created_at`. `(dataset_version_id, source_feature_id)` уникален внутри конкретного слоя, если внешний id известен.

Поля, на которые будут опираться алгоритмы, вынесены из JSON в типизированные колонки. Roads имеют `road_class`, `name`, `lanes`, `max_speed_kph`, `one_way`; buildings — `building_class`, `name`, `levels`, `height_m`; landuse/water/facilities имеют соответствующий class, а facilities дополнительно `name` и `capacity`. Source constraints хранят стабильные `constraint_code`, `severity` (`HARD`/`SOFT`) и `scope`, согласованные с core constraint contract.

Road geometry нормализуется к `MULTILINESTRING`, building/landuse — к `MULTIPOLYGON`; water/facility/constraint используют `GEOMETRY`, поскольку допустимая топология зависит от типа источника. PostgreSQL trigger требует, чтобы SRID каждой записываемой геометрии совпадал с `Project.working_srid` через цепочку `DatasetVersion -> Dataset -> Project`.

До перехода `DatasetVersion` в `ready` ingest может вставлять, заменять и очищать строки для retry-safe нормализации. После `status = ready` source rows этой версии становятся immutable: `INSERT`, `UPDATE` и `DELETE` запрещены. Перенос строки на другой `dataset_version_id` запрещён всегда.

S02-T10 добавляет каждому source layer составной B-tree `(dataset_version_id, id)` для version-scoped/keyset access и GiST по `geometry` для bbox/spatial predicates. Integrity unique `(dataset_version_id, source_feature_id)` остаётся отдельным контрактом и не заменяет access indexes. Подробная policy зафиксирована в `docs/SPATIAL_INDEXING.md`.

## Следующие сущности

Следующий work item S02-T11 вводит repository/application service boundary: controllers не должны содержать SQL query details, а прикладные сервисы должны тестироваться без HTTP. Площади и расстояния считаются только в метрической рабочей CRS проекта.
