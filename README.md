# Media Dataset Factory

Внутренняя платформа для подготовки, проверки, упаковки и доставки видеоассетов. Полные продуктовые требования находятся в [ТЗ](./ТЗ_платформа_подготовки_медиаассетов.md).

## Текущий вертикальный срез

- потоковая загрузка видео на локальное хранилище;
- SHA-256 во время загрузки;
- обнаружение точного дубля по хешу;
- инспекция через `ffprobe`;
- проверка word-level таймингов транскрипта;
- API `/api/v1` и health checks;
- unit- и API-тесты.
- постоянные комплекты и optimistic locking переходов;
- устойчивые фоновые задания с `Idempotency-Key`;
- Redis/RQ worker, связанный со state machine.
- внутренний proxy без изменения мастер-файла;
- определение сцен и нарезка перекрывающихся клипов;
- заменяемый VLM provider и сохраняемые analysis runs;
- локальный Qwen3-VL через OpenAI-совместимый endpoint;
- единая временная шкала с дедупликацией, provenance и конфликтами;
- операторское утверждение событий и analysis run;
- версионируемые семантические метаданные, категории и главы;
- версионируемые сценарии озвучки и обязательное утверждение перед TTS;
- явная аудиополитика для будущей сборки мастер-файла;
- сборка и валидация мастер-файла без перекодирования видеопотока;
- локальная ASR-транскрибация master через faster-whisper;
- привязка транскрипта к SHA-256 мастера и блокирующая валидация слов.
- версионная сборка metadata/transcript/manifest и автоприёмка до QA;
- операторская QA-приёмка конкретной версии сборки;
- потоковый локальный ZIP-экспорт только после успешной QA-приёмки;
- управляемая доставка master → sidecars с удалённой сверкой и безопасным retry;
- адаптивная операторская консоль с загрузкой MP4, очередью комплектов,
  технической карточкой, прогрессом jobs и базовыми запусками pipeline.
- ручная приёмка VLM-событий с evidence и таймкодами, утверждение analysis run,
  версионное редактирование и утверждение семантических метаданных в UI.
- UI-подготовка сценария озвучки, запуск TTS, выбор аудиополитики и постановка
  master в сборку, включая ручной перезапуск восстановимых стадий.
- финальная QA-карточка вычисленных метаданных и файлов, reject/approve,
  локальный ZIP, доставка, контроль статуса и безопасный retry в UI.
- неизменяемый аудит ручных запросов и переходов состояния, correlation ID,
  структурированные HTTP-логи и опциональный RBAC по API-ключам.

## Требования

- Python 3.12+;
- FFmpeg/ffprobe для реальной инспекции видео;
- optional extra `asr` для локального faster-whisper;
- SQLite для локального режима или PostgreSQL для production.

## Запуск

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn media_factory.api.app:app --reload
```

Операторская консоль после запуска доступна по адресу
[`http://127.0.0.1:8000/`](http://127.0.0.1:8000/). Она обслуживается тем же FastAPI-процессом
и не требует отдельного Node.js-сервиса. OpenAPI остаётся доступен на `/docs`.

Проверка:

```bash
curl http://127.0.0.1:8000/api/v1/health/live
curl http://127.0.0.1:8000/api/v1/health/ready
```

Загрузка видео:

```bash
curl -F 'file=@example.mp4' http://127.0.0.1:8000/api/v1/assets/uploads
```

Создание комплекта и запуск повторной инспекции:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/packages \
  -H 'Content-Type: application/json' \
  -d '{"source_asset_id":"<asset-id>"}'

curl -X POST http://127.0.0.1:8000/api/v1/packages/<package-id>/transitions \
  -H 'Content-Type: application/json' \
  -d '{"target":"inspecting","expected_version":1}'

curl -X POST http://127.0.0.1:8000/api/v1/packages/<package-id>/jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: inspect-<package-id>-v1' \
  -d '{"kind":"inspect_asset","payload":{}}'
```

Если `ffprobe` не установлен, `live` остаётся успешным, но `ready` возвращает состояние `not_ready`, а попытка инспекции завершается объяснимой ошибкой зависимости.

## Локальный Qwen3-VL

Модель запускается отдельным GPU-процессом, например через vLLM:

```bash
vllm serve Qwen/Qwen3-VL-8B-Instruct \
  --revision e0a319f4d147b3916275a053b0583ca82f351e90 \
  --port 8001 \
  --max-model-len 32768
```

В `.env` переключите worker с тестового провайдера на реальный:

```dotenv
MEDIA_FACTORY_VLM_PROVIDER=qwen
MEDIA_FACTORY_ALLOW_FAKE_VLM=false
MEDIA_FACTORY_QWEN_BASE_URL=http://localhost:8001/v1
MEDIA_FACTORY_QWEN_MODEL=Qwen/Qwen3-VL-8B-Instruct
MEDIA_FACTORY_QWEN_MODEL_REVISION=e0a319f4d147b3916275a053b0583ca82f351e90
```

После задания `analyze_video` клипы доступны по
`GET /api/v1/analysis-runs/{run_id}/clips`, а объединённые события — по
`GET /api/v1/analysis-runs/{run_id}/events`. Веса модели и пользовательские
видео в Git не добавляются.

## Проверка анализа и метаданных

Каждое событие нужно утвердить или отклонить через
`PATCH /api/v1/analysis-events/{event_id}`. После этого analysis run утверждается
через `POST /api/v1/analysis-runs/{run_id}/reviews`. Неутверждённый анализ не
может стать источником метаданных.

Черновик создаётся через
`POST /api/v1/packages/{package_id}/metadata/proposals`. Изменения сохраняются
как новые версии через `POST /api/v1/metadata/{metadata_id}/revisions`, а
финальное подтверждение выполняется через
`POST /api/v1/metadata/{metadata_id}/approve`. Утверждённая версия становится
неизменяемой. Технические поля — длительность, разрешение и контрольные суммы —
не генерируются из VLM.

## Версионируемая схема заказчика

Активная схема загружается по неизменяемому пути
`schemas/<customer>/<version>.json`; встроенная пилотная версия —
`internal@1.0`. Она задаёт порядок ключей метаданных, обязательные файлы,
контейнеры, категории, аудиополитики, допустимость синтетического голоса,
главы и допуск длительности. Текущая схема доступна через
`GET /api/v1/customer-schemas`, а её SHA-256 фиксируется в build и manifest.

Для внешней директории задайте `MEDIA_FACTORY_CUSTOMER_SCHEMA_DIR`. Изменение
активной версии выполняется созданием нового JSON-файла и сменой
`MEDIA_FACTORY_DEFAULT_CUSTOMER_SCHEMA_VERSION`; существующие сборки сохраняют
исходную версию и хеш. Базовое имя строится из категории и заголовка, а
sidecar-файлы используют суффиксы `_transcript`, `_metadata`, `_manifest`.

## Озвучка и аудиополитика

Для утверждённых метаданных с `narration_language` сценарий создаётся через
`POST /api/v1/packages/{package_id}/narration/proposals`. Правки сохраняются как
новые версии, а TTS-job `generate_narration` принимается только с `script_id`
утверждённого сценария.

Development-провайдер создаёт маркированную синтетическую WAV-дорожку без речи.
Fake-TTS запрещён вне development. Для реальной локальной речи используйте
`MEDIA_FACTORY_TTS_PROVIDER=piper`, установите Piper и передайте путь к
зафиксированной `.onnx`-модели через `MEDIA_FACTORY_PIPER_MODEL_PATH`; текст
остаётся в локальном контуре. После проверки дорожки оператор фиксирует
один из режимов `preserve`, `remove`, `replace` или `additional` через
`POST /api/v1/packages/{package_id}/audio-decisions`. Режим `additional` по
умолчанию отключён до появления разрешающей customer schema.

## Сборка мастер-файла

`POST /api/v1/packages/{package_id}/build-master` создаёт фоновое задание после
фиксации аудиополитики. Сборщик всегда использует `-c:v copy`; для аудио
применяется выбранный режим. Полученный файл повторно инспектируется, его
видеопараметры и длительность сравниваются с исходником, все потоки проходят
decode-pass, после чего вычисляется SHA-256. Только успешное прохождение всех
проверок переводит комплект в `master_ready`. Неуспешный производный файл
удаляется, а попытка сохраняется со статусом `failed`.

## Транскрибация финального мастера

`POST /api/v1/packages/{package_id}/transcribe` создаёт фоновое задание только
для последней успешной сборки master. Перед запуском ASR файл повторно
хешируется; транскрипт сохраняет SHA-256 и идентификатор именно этой
сборки. Адаптер `faster-whisper` всегда запрашивает `word_timestamps=True`,
сохраняет язык, вероятность его определения, модель, версию провайдера и
параметры запуска.

После ASR выполняется блокирующая проверка таймингов. Отсутствие слов создаёт
ошибку `no_word_timings`, неверные или выходящие за длительность значения —
`invalid_word_timing`; в обоих случаях пакет недоступен для упаковки. История
запусков доступна через `GET /api/v1/packages/{package_id}/transcription-runs`.

Для локального рабочего ASR укажите `MEDIA_FACTORY_ASR_PROVIDER=faster-whisper`.
Параметры модели задаются переменными `MEDIA_FACTORY_FASTER_WHISPER_*`. Режим
`fake` предназначен только для разработки. Веса модели загружаются или монтируются
во время выполнения и не должны добавляться в Git.

## Сборка и автоматическая приёмка комплекта

`POST /api/v1/packages/{package_id}/build` создаёт новую неизменяемую версию
комплекта. В директории `vNNNN` материализуются master, транскрипт,
метаданные и манифест с единым безопасным базовым именем. Master
копируется потоково в версионную директорию, чтобы последующее изменение
рабочего master не изменило уже собранную версию.

Технические поля `Duration`, `Resolution`, `Language` и `WPM` вычисляются из
зафиксированных артефактов. `Publication Date` остаётся `null`, пока не выбрано
правило клиентской схемы. Манифест хранит версии входов, моделей и
промпта, хеши/размеры поставляемых файлов и результаты валидации. Свой
хеш манифест хранит в записи build, а не внутри себя.

Валидатор повторно проверяет SHA-256 и декодирование master, word timings,
связь транскрипта с master, утверждение метаданных/анализа, аудиополитику,
пути, имена и дубли. Ошибка переводит комплект в `validation_failed`; только
успешная приёмка переводит его в `awaiting_qa`. История сборок доступна через
`GET /api/v1/packages/{package_id}/builds`.

## QA-приёмка и локальный экспорт

Оператор принимает или отклоняет конкретную версию сборки через
`POST /api/v1/packages/{package_id}/approve`. В теле запроса обязательны
`package_build_id`, `approved` и `reviewer`; при отклонении также обязательна
непустая `reason`. Перед фиксацией решения сервис заново проверяет наличие,
размеры и SHA-256 всех файлов. Решение QA, итоговое состояние комплекта и новый
хеш финализированного манифеста записываются одной транзакцией. Одна версия
сборки может получить только одно неизменяемое QA-решение.

Одобренный комплект переводится в `validated`, отклонённый — в
`validation_failed`. Финальный манифест содержит решение, проверяющего и время
проверки. История доступна через
`GET /api/v1/packages/{package_id}/qa-reviews`.

Локальный ZIP создаётся фоновым заданием:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/package-builds/<package-build-id>/exports \
  -H 'Idempotency-Key: export-<package-build-id>-v1'
```

Экспорт разрешён только для одобренной версии. Непосредственно перед записью
архива файлы и манифест повторно сверяются с зафиксированными хешами. Архив
записывается потоково с поддержкой ZIP64, существующие архивы не
перезаписываются. Состояние экспорта доступно через
`GET /api/v1/package-builds/{package_build_id}/exports`, готовый файл — через
`GET /api/v1/exports/{export_id}/download`.

## Доставка комплекта

Доставка запускается только для последней одобренной версии сборки:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/packages/<package-id>/deliver \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: delivery-<package-build-id>-v1' \
  -d '{"package_build_id":"<package-build-id>","prefix":"2026/customer/session"}'
```

Worker повторно проверяет локальные SHA-256, загружает единственный master и
только после его подтверждения начинает sidecar-файлы. Затем каждый удалённый
объект сверяется по размеру и SHA-256. Состояние `complete` и
`package_complete_at` устанавливаются только после успешной сверки всего
комплекта. Существующие удалённые ключи не перезаписываются и не удаляются.

Неудачная попытка сохраняет уже подтверждённые объекты. Повтор запускается с
новым ключом идемпотентности через
`POST /api/v1/deliveries/{delivery_id}/retry`; ранее записанный master
проверяется на стороне назначения и не загружается заново. Статус и фактические
объекты доступны через `GET /api/v1/deliveries/{delivery_id}` и
`GET /api/v1/deliveries/{delivery_id}/objects`.

В пилотном режиме используется файловый адаптер. Корень назначения задаётся
через `MEDIA_FACTORY_DELIVERY_FILESYSTEM_ROOT`, размер потоковой части — через
`MEDIA_FACTORY_DELIVERY_PART_SIZE` (по умолчанию 32 МиБ). Провайдерный код
изолирован от бизнес-логики доставки.

Для S3 установите optional extra и задайте bucket:

```bash
.venv/bin/pip install -e '.[delivery-s3]'
```

```dotenv
MEDIA_FACTORY_DELIVERY_PROVIDER=s3
MEDIA_FACTORY_DELIVERY_CHECKPOINT_SECRET=<random-secret-at-least-32-characters>
MEDIA_FACTORY_S3_BUCKET=customer-delivery
MEDIA_FACTORY_S3_REGION=eu-central-1
```

`MEDIA_FACTORY_S3_ENDPOINT_URL` позволяет подключить S3-совместимый сервис.
Адаптер использует multipart для больших файлов, SHA-256 каждого part и
условие `If-None-Match: *` при создании объекта. Существующий ключ приводит к
ошибке доставки, а не к перезаписи. AWS credentials берутся из стандартной
credential chain SDK и не должны записываться в `.env` или Git.

Для GCS:

```bash
.venv/bin/pip install -e '.[delivery-gcs]'
```

```dotenv
MEDIA_FACTORY_DELIVERY_PROVIDER=gcs
MEDIA_FACTORY_DELIVERY_CHECKPOINT_SECRET=<random-secret-at-least-32-characters>
MEDIA_FACTORY_GCS_BUCKET=customer-delivery
MEDIA_FACTORY_GCS_PROJECT=example-project
```

GCS-адаптер использует resumable upload, checksum-проверку и
`if_generation_match=0`, поэтому существующий объект не заменяется. Доступ
получается через Application Default Credentials.

Незавершённые S3 parts и GCS byte offset сохраняются в
`upload_checkpoints`. После перезапуска worker повторная попытка сверяет
состояние с облаком и продолжает с первой неподтверждённой части.
S3 UploadId и GCS session URI хранятся в БД только в зашифрованном виде;
`MEDIA_FACTORY_DELIVERY_CHECKPOINT_SECRET` нельзя коммитить в Git. Потеря или
замена этого ключа делает активные checkpoints нечитаемыми.

## Доступ и аудит

В локальной разработке `MEDIA_FACTORY_AUTH_ENABLED=false`: интерфейс работает
как локальный администратор. Для пилота включите auth и передайте три разные
случайные строки через secret manager или переменные окружения:

```dotenv
MEDIA_FACTORY_AUTH_ENABLED=true
MEDIA_FACTORY_OPERATOR_API_KEY=<operator-secret>
MEDIA_FACTORY_QA_API_KEY=<qa-secret>
MEDIA_FACTORY_ADMIN_API_KEY=<admin-secret>
```

Ключ вводится в поле `API key` в шапке консоли и хранится только в
`sessionStorage` вкладки. API-клиент передаёт его в `X-API-Key`. Оператор может
запускать и редактировать pipeline, роль QA — утверждать или отклонять готовую
сборку, администратор имеет оба набора прав и доступ к глобальному аудиту.
Каждый ответ содержит `X-Request-ID`; ручные запросы и все переходы состояния
записываются в append-only `audit_events` без тел запросов и секретов.

## Наблюдаемость

`GET /api/v1/metrics/summary` возвращает состояния комплектов/jobs/доставок,
среднее время завершённых этапов, глубину очереди и список jobs без обновлений
дольше `MEDIA_FACTORY_STUCK_JOB_SECONDS`. Счётчик застрявших работ виден в
шапке консоли. `GET /metrics` отдаёт те же основные показатели в текстовом
Prometheus-формате. HTTP-запросы логируются однострочным JSON с длительностью,
ролью и correlation ID; тела запросов, API-ключи и секреты не логируются.

## Тесты

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
```

Тесты не требуют реального FFmpeg: внешний процесс подменяется fake runner.
Отдельный smoke-тест запускает настоящий FFmpeg, если бинарники доступны; в CI
они устанавливаются явно. Полный порядок развёртывания и ручной сквозной
приёмки описан в [pilot runbook](docs/PILOT_RUNBOOK.md).

## Запуск через контейнеры

Production-подобный локальный запуск использует PostgreSQL, Redis, API с
FFmpeg и отдельный background worker:

```bash
docker compose up --build
```

После миграций API доступен на `http://127.0.0.1:8000`, а OpenAPI — на
`http://127.0.0.1:8000/docs`. Пароль в `compose.yaml` предназначен только для
локальной разработки и должен заменяться секретом в любой внешней среде.
