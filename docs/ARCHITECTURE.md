# Архитектура

## Компоненты

```text
React + MapLibre
      |
      v
   FastAPI  ---- PostgreSQL/PostGIS
      |               |
      |               +-- проекты, datasets, геометрии, результаты
      |
      +---- Redis ---- Worker (ARQ)
                        |
                        v
                  Algorithmic Core
      suitability -> zoning -> roads -> blocks -> buildings
                 -> population -> infrastructure -> validation -> metrics
```

## Границы модулей

`core/urban_generator` не импортирует FastAPI, SQLAlchemy или React-специфичные типы. Ядро получает нормализованные входные данные и конфигурацию и возвращает пространственные результаты/метрики.

`backend` отвечает за HTTP, валидацию запросов, транзакции, хранение файлов, создание jobs и сериализацию результатов.

`worker` является отдельным процессом. Генерация кварталов, перерасчёт доступности и растровый анализ могут занимать минуты и не должны блокировать HTTP worker.

`frontend` работает как GIS-интерфейс: карта является центральным элементом, а панели управляют слоями, параметрами, сценариями и метриками.

## Backend request boundary

HTTP-контроллеры не строят SQL statements и не управляют ORM query details. Для прикладных use cases используется направление зависимостей:

```text
FastAPI endpoint
    -> application service
        -> repository protocol
            -> SQLAlchemy repository adapter
                -> request-scoped Session
```

Контроллер отвечает за HTTP/Pydantic boundary и отображение прикладных ошибок в HTTP status codes. `ProjectService` реализует project CRUD orchestration и тестируется с fake repository без FastAPI и DB session. `SqlAlchemyProjectRepository` владеет `select/order/limit/offset`, ORM persistence и commit/refresh для project mutations.

Инфраструктурные SQL probes также находятся за DB boundary: readiness endpoint вызывает bounded database probe и не содержит SQL text. Более сложные transaction/unit-of-work abstractions добавляются только когда use case требует координации нескольких repositories; S02-T11 не вводит преждевременный generic UoW.

## Обязательный конечный продукт

Полноценный 2D-сервис: импорт реальных данных, CRS/валидация, все стадии генерации, инфраструктура и демография, несколько сценариев, прогресс jobs, интерактивная карта, сравнение, экспорт, тесты и воспроизводимость.

Не требуется: BIM, моделирование интерьеров/квартир, детальная архитектура фасадов и обязательная 3D-сцена.
