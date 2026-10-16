#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
# HTTPS (self-signed or your own certificate, see .env). Set ARS_SSL=0 in .env for plain HTTP via gunicorn.
if [ "$(grep -E '^ARS_SSL=' .env 2>/dev/null | tail -1 | cut -d= -f2)" = "0" ]; then
  exec gunicorn --workers 1 --bind 0.0.0.0:${PORT:-5001} --timeout 120 wsgi:app
fi
exec python serve_https.py
