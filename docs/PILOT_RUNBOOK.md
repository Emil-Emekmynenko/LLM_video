# Media Dataset Factory — pilot runbook

## 1. Что считается рабочим пилотом

Пилот готов, когда API, PostgreSQL, Redis и worker запущены; FFmpeg/ffprobe
доступны; активная customer schema загружается; один тестовый MP4 проходит
путь `uploaded → complete`; QA выполняется отдельным ключом; ZIP скачивается;
доставка содержит master, transcript, metadata и manifest с совпадающими
SHA-256. Проверка реальных VLM/ASR/TTS выполняется отдельно от deterministic
fake-провайдеров.

## 2. Подготовка

1. Скопировать `.env.example` в `.env` и не коммитить полученный файл.
2. Для локальной демонстрации оставить fake providers и filesystem delivery.
3. Для внешнего пилота включить RBAC, задать разные operator/QA/admin keys,
   выключить fake VLM/ASR/TTS и настроить Qwen, faster-whisper и Piper.
4. Установить FFmpeg/ffprobe 9.x либо использовать контейнер из репозитория.
5. Модель Piper `.onnx` и соседний `.onnx.json` хранить вне Git; путь передать
   через `MEDIA_FACTORY_PIPER_MODEL_PATH`.

Проверка окружения:

```bash
.venv/bin/python scripts/pilot_check.py
.venv/bin/python scripts/pilot_check.py --external
```

Первая команда проверяет development-пилот, вторая добавляет требования к
внешним моделям, RBAC и шифрованию cloud checkpoints.

## 3. Запуск

```bash
docker compose up --build -d
docker compose ps
curl -fsS http://127.0.0.1:8000/api/v1/health/ready
curl -fsS http://127.0.0.1:8000/metrics
```

API сам выполняет `alembic upgrade head` перед стартом. Для запуска без Docker:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn media_factory.api.app:app --host 127.0.0.1 --port 8000
.venv/bin/python -m media_factory.workers.main
```

## 4. Сквозная приёмка

1. Открыть `http://127.0.0.1:8000/`, ввести operator key.
2. Загрузить короткий неперсональный MP4 и дождаться инспекции.
3. Запустить анализ, проверить каждое событие и утвердить analysis run.
4. Отредактировать и утвердить метаданные.
5. Выбрать preserve/remove либо утвердить сценарий, запустить Piper и выбрать
   replace.
6. Собрать master, транскрибировать его и собрать комплект.
7. Сменить ключ на QA, проверить вычисленные поля и утвердить build.
8. Вернуть operator key, создать ZIP и запустить filesystem delivery.
9. Убедиться, что статус `complete`, `package_complete_at` заполнен, у всех
   uploaded objects есть `verified_at`, а `/metrics` не показывает stuck jobs.
10. Проверить аудит карточки: ручные правки, approvals и переходы состояния.

Автоматическая приёмка:

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
```

В CI FFmpeg устанавливается явно, поэтому `test_ffmpeg_smoke.py` не
пропускается: он создаёт реальный H.264/AAC MP4, строит proxy и clip, собирает
master с `-c:v copy`, повторно инспектирует и полностью декодирует его.

## 5. Восстановление

- Ошибочный job: исправить причину и нажать доступный retry в карточке.
- `validation_failed`: проверить код issue, затем повторить transcription или
  package build; старая версия артефактов остаётся неизменной.
- `delivery_failed`: повторить delivery; подтверждённые части не загружаются
  заново, sidecars не опережают master.
- Потерянный checkpoint encryption key: активную cloud-сессию восстановить
  нельзя; создать новую доставку после ручной проверки удалённых ключей.
- Откат приложения: вернуть предыдущий image; миграции не откатывать без
  резервной копии PostgreSQL и явного плана совместимости.

## 6. Данные и безопасность

Не использовать клиентское видео для разработки или обучения без письменного
разрешения. Не добавлять в Git медиа, модели, `.env`, API keys, cloud
credentials и delivery checkpoints. Платформа не удаляет и не перезаписывает
удалённые объекты; конфликт ключа считается ошибкой поставки.
