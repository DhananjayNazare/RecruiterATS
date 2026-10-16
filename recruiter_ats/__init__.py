"""Recruiter ATS application package."""
from recruiter_ats.config import app
from recruiter_ats.database import init_db
from recruiter_ats import routes  # noqa: F401  (registers every route)
from recruiter_ats.services.backup import backup_worker
from recruiter_ats.services.mailer import email_worker

__all__ = ["app", "init_db", "backup_worker", "email_worker"]
