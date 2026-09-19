#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn media_factory.api.app:app --host 0.0.0.0 --port 8000

