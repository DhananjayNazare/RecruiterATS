"""Application object, paths, constants and optional libraries shared by every module."""
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

try:
    from PIL import Image
except ImportError:          # photo compression is optional
    Image = None
try:
    import pyotp
    import qrcode
except ImportError:          # two-factor sign-in is optional
    pyotp = None
    qrcode = None

# Project root (the folder that holds templates/, static/, uploads/ and the database).
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DB_PATH = BASE_DIR / "recruiter_ars.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.secret_key = os.getenv("ARS_SECRET_KEY", "change-this-secret-key-in-production")
if os.getenv("ARS_ENV", "development").lower() == "production" and app.secret_key == "change-this-secret-key-in-production":
    raise RuntimeError("ARS_SECRET_KEY must be set to a strong random value in production.")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies are on by default when HTTPS is enabled (ARS_SSL=1); ARS_SESSION_COOKIE_SECURE overrides.
    SESSION_COOKIE_SECURE=os.getenv("ARS_SESSION_COOKIE_SECURE", os.getenv("ARS_SSL", "0")).strip().lower() in {"1", "true", "yes", "on"},
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)

# A batch can contain up to 10 resumes, each strictly capped at 2 MiB.
# Keep the request cap above 20 MiB to allow multipart overhead, while each
# resume is independently enforced by save_resume_upload().
MAX_RESUME_BYTES = 2 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

ROLES = ["Recruiter", "Manager", "Admin", "Interviewer", "Candidate"]

PERMISSIONS = [
    ("manage_users", "Manage Users", "Activate, deactivate, delete users and set their permissions"),
    ("manage_jobs", "Manage Jobs", "Create, edit and close job postings"),
    ("manage_candidates", "Manage Candidates", "Add, edit and delete candidate records"),
    ("manage_interviews", "Manage Interviews", "Schedule and update interview rounds"),
    ("view_reports", "View Reports", "Access recruitment analytics and reports"),
    ("send_emails", "Schedule Status Emails", "Schedule status-update emails to candidates and interviewers"),
]

# ---------------------------------------------------------------------------
# Positions (formerly "Jobs"), AI screening pipeline, auto interview scheduling
# ---------------------------------------------------------------------------
SCHEDULE_LOCK = threading.Lock()   # serialises slot selection so two workers never book the same slot

BATCH_LOCK = threading.Lock()      # serialises progress updates for batch runs


# Voice-command service (separate typed password; locks itself when idle and on logout)
VOICE_IDLE_SECONDS = int(os.getenv("ARS_VOICE_IDLE_SECONDS", "300") or 300)
