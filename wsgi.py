"""Production WSGI entry point for Recruiter ATS.

For Windows, run with Waitress:
    waitress-serve --host=0.0.0.0 --port=5001 wsgi:app

For Linux, run with Gunicorn:
    gunicorn --workers 1 --bind 0.0.0.0:5001 --timeout 120 wsgi:app
"""
import os
import threading
from recruiter_ats import app, init_db, backup_worker, email_worker

init_db()

# Keep one application worker in the production setup so the built-in
# scheduled backup/email workers are not duplicated across WSGI processes.
if os.getenv("ARS_RUN_BACKGROUND_WORKERS", "1") == "1":
    threading.Thread(target=backup_worker, daemon=True, name="backup-worker").start()
    threading.Thread(target=email_worker, daemon=True, name="email-worker").start()
