# AGENTS.md

## Project

Media Dataset Factory is an internal platform for turning source video into a validated delivery package. The product specification is in `ТЗ_платформа_подготовки_медиаассетов.md`.

## Non-negotiable rules

- Never commit customer media, generated masters, transcripts containing customer data, model weights, secrets, or `.env` files.
- Never silently transcode the delivery master's video stream. Master-building code must use stream copy unless an approved customer profile explicitly permits transcoding.
- A transcript must be produced from the current final master and linked to its SHA-256.
- Missing word-level timestamps are a blocking `no_word_timings` validation error.
- Delivery order is media first, then sidecars. Never overwrite or delete a remote customer object by default.
- Technical metadata comes from artifacts and deterministic code, never from a language model.
- Keep provider-specific ML and storage code behind interfaces.

## Local commands

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/alembic upgrade head
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest
.venv/bin/uvicorn media_factory.api.app:app --reload
```

## Development workflow

- Implement vertical slices with tests.
- Use `apply_patch` for manual edits.
- Preserve unrelated user changes.
- Update documentation and tests with behavior changes.
- Do not weaken blocking validation to make a happy-path test pass.
