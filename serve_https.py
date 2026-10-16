"""Production HTTPS server (Windows and Linux):  python serve_https.py

Uses Cheroot (pure Python, TLS built in) to serve wsgi:app on https://0.0.0.0:PORT.
One worker only, because the backup/email background threads must not be duplicated.
Certificate: your own (ARS_SSL_CERT / ARS_SSL_KEY) or an auto-created self-signed one in certs/.
"""
import os

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")   # so ARS_SSL=0 in .env is honoured
os.environ.setdefault("ARS_SSL", "1")     # HTTPS by default; set before the app package reads its settings

from cheroot.wsgi import Server
from cheroot.ssl.builtin import BuiltinSSLAdapter

from recruiter_ats.ssl_setup import get_ssl_files
from wsgi import app    # importing wsgi runs init_db() and starts the background workers

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    server = Server(("0.0.0.0", port), app, numthreads=16, request_queue_size=64, timeout=120)
    ssl_files = get_ssl_files()
    if ssl_files:
        server.ssl_adapter = BuiltinSSLAdapter(certificate=ssl_files[0], private_key=ssl_files[1])
    print(f"[ATS] Serving {'https' if ssl_files else 'http'}://0.0.0.0:{port}  (Ctrl+C to stop)")
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
