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

## Требования

- Python 3.9+ для текущего прототипа;
- FFmpeg/ffprobe для реальной инспекции видео;
- рекомендуемая production-версия Python — 3.12+.

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
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

Если `ffprobe` не установлен, `live` остаётся успешным, но `ready` возвращает состояние `not_ready`, а попытка инспекции завершается объяснимой ошибкой зависимости.

## Тесты

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
```

Тесты не требуют реального FFmpeg: внешний процесс подменяется fake runner.

