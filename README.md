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
