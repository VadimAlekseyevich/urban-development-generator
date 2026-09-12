# Модель данных

Система разделяет логические datasets, их неизменяемые версии, нормализованные пространственные объекты и результаты конкретных запусков генерации. Каждый запуск воспроизводим через `seed`, нормализованную JSON-конфигурацию, её schema version, рабочую CRS, конкретные версии входных наборов и commit SHA приложения.

## Project

Контейнер исследования: название, описание, граница территории, metadata границы и явный `working_srid`. Метрические операции не выполняются без валидной проектной CRS.

## Dataset

Логический набор данных внутри проекта. Он хранит устойчивую идентичность набора (`project_id`, `kind`) и не содержит upload-specific полей, статуса файла или filesystem path.

## DatasetVersion

Конкретная версия источника для `Dataset`: порядковый `version`, SHA-256 checksum, lifecycle `status`, `source_metadata` и время создания. Идентичность и содержимое версии не редактируются in-place; при изменении входных данных создаётся новая версия. Lifecycle `status` может обновляться отдельно.

Legacy datasets из ранней схемы мигрируют в `version = 1`. Если исторический checksum неизвестен, он остаётся `NULL`, а не вычисляется или подставляется фиктивно. Старый `storage_path` сохраняется только как legacy metadata при миграции; новая модель не имеет first-class filesystem path. Полноценные artifact URI/state появятся в S02-T05.

## GenerationRun

Воспроизводимый запуск хранит `project_id`, lifecycle `status`, `mode`, `seed`, snapshot `working_srid`, нормализованный `config_json`, `config_schema_version`, commit SHA приложения, timestamps, метрики и диагностику ошибок. Входные данные фиксируются ссылками на конкретные `DatasetVersion` через `generation_run_dataset_versions`, а не на изменяемый логический `Dataset`.

`mode` ограничен значениями `EXPANSION` и `FROM_SCRATCH`. Успешный run (`status = succeeded`) должен иметь commit SHA. После перехода в успешное состояние scalar-поля run и набор ссылок на `DatasetVersion` неизменяемы; защита существует как в ORM, так и на уровне PostgreSQL trigger. Добавить или удалить dataset-version ref успешного запуска нельзя. DatasetVersion, уже используемую запуском, нельзя удалить через FK `RESTRICT`.

Миграция legacy `GenerationRun` присваивает историческим строкам `mode = EXPANSION`, `config_schema_version = legacy-v0` и копирует `working_srid` из проекта. Старое `code_version` переносится в `commit_sha` только если уже является 40-символьным hex commit SHA; неизвестная или произвольная версия не подменяется фиктивным SHA.

## Следующие сущности

Дальнейшие миграции добавят run stage results, artifact lifecycle, jobs/outbox, canonical source layers и generated roads/blocks/buildings/infrastructure. Пространственные слои получают GiST-индексы; площади и расстояния считаются только в метрической рабочей CRS проекта.
