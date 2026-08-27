#!/usr/bin/env bash
set -o errexit

python -m pip install --disable-pip-version-check uv
uv sync --frozen --no-dev
uv run python manage.py collectstatic --no-input \
  --ignore css/input.css \
  --ignore node_modules \
  --ignore package.json \
  --ignore package-lock.json
uv run python manage.py migrate
