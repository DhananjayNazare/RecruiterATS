#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-linux.txt
if [ ! -f .env ]; then cp .env.example .env; fi
SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')
echo "Generated production secret: $SECRET"
echo "Set ARS_SECRET_KEY in .env to this value."
echo "Run HTTPS: ./run_production_linux.sh  (or: python serve_https.py)"
