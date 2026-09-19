FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

RUN python -m pip install --no-cache-dir '.[asr]' \
    && chmod +x /app/scripts/start-api.sh

EXPOSE 8000

CMD ["/app/scripts/start-api.sh"]
