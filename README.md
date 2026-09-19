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

## Требования

- Python 3.12+;
- FFmpeg/ffprobe для реальной инспекции видео;
- SQLite для локального режима или PostgreSQL для production.

## Запуск

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn media_factory.api.app:app --reload
```

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

## Тесты

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
```

Тесты не требуют реального FFmpeg: внешний процесс подменяется fake runner.

## Запуск через контейнеры

Production-подобный локальный запуск использует PostgreSQL, Redis, API с
FFmpeg и отдельный background worker:

```bash
docker compose up --build
```

После миграций API доступен на `http://127.0.0.1:8000`, а OpenAPI — на
`http://127.0.0.1:8000/docs`. Пароль в `compose.yaml` предназначен только для
локальной разработки и должен заменяться секретом в любой внешней среде.
