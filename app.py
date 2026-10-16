"""Development entry point:  python app.py   ->  https://localhost:5001

HTTPS is on by default (self-signed certificate created in certs/, or your own via ARS_SSL_CERT / ARS_SSL_KEY).
Set ARS_SSL=0 in .env to use plain HTTP. Production: serve_https.py (see README_PRODUCTION.md).
"""
import os
import threading

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")   # so ARS_SSL=0 in .env is honoured
os.environ.setdefault("ARS_SSL", "1")     # HTTPS by default; set before the app package reads its settings

from recruiter_ats import app, init_db, backup_worker, email_worker
from recruiter_ats.ssl_setup import get_ssl_files

if __name__ == "__main__":
    init_db()
    threading.Thread(target=backup_worker, daemon=True, name="backup-worker").start()
    threading.Thread(target=email_worker, daemon=True, name="email-worker").start()
    port = int(os.getenv("PORT", "5001"))
    ssl_files = get_ssl_files()
    print(f"[ATS] Open {'https' if ssl_files else 'http'}://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_files)
