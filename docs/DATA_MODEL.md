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

Ошибки сохраняются без разбора текста исключения: `error_class` соответствует стабильной core taxonomy (`domain`, `config`, `data`, `transient`, `permanent`, `cancelled`), `error_code` хранит машинный code, а `error_json` — message/details и признаки `retryable`/`cancelled`. Решение о повторной попытке и enqueue semantics остаётся за последующими application/dispatcher work items; S02-T06 фиксирует только authoritative state и idempotency boundary.

## Следующие сущности

Дальнейшие миграции добавят outbox, canonical source layers и generated roads/blocks/buildings/infrastructure. Пространственные слои получают GiST-индексы; площади и расстояния считаются только в метрической рабочей CRS проекта.
