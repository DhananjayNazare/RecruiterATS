"""Shared test setup: every test run gets its own throw-away database and upload folder."""
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recruiter_ats import app, config, init_db  # noqa: E402
from recruiter_ats.database import db  # noqa: E402

PASSWORD = "Pass@1234"
USERS = {
    "recruiter": ("Rita Recruiter", "recruiter@test.local", "Recruiter"),
    "interviewer": ("Ivan Interviewer", "interviewer@test.local", "Interviewer"),
    "candidate": ("Cara Candidate", "candidate@test.local", "Candidate"),
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh database with an admin, a hidden super user and one user per role."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
    monkeypatch.setenv("ARS_SUPERUSER_EMAIL", "super@test.local")
    monkeypatch.setenv("ARS_SUPERUSER_PASSWORD", "Sup3r!pass")
    init_db()

    con = db()
    for name, email, role in USERS.values():
        con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,'2026-01-01')",
                    (name, email, generate_password_hash(PASSWORD), role))
    con.commit()
    con.close()
    app.config.update(TESTING=True)
    return app


def user_id(email):
    con = db()
    row = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    con.close()
    return row["id"]


class Client:
    """Test client that is already signed in and can submit forms with a valid CSRF token."""

    def __init__(self, email):
        self.http = app.test_client()
        with self.http.session_transaction() as sess:
            sess["user_id"] = user_id(email)
            sess["_csrf_token"] = "test-token"

    def get(self, url, **kw):
        return self.http.get(url, **kw)

    def post(self, url, **data):
        data["_csrf_token"] = "test-token"
        return self.http.post(url, data=data, content_type="multipart/form-data")


@pytest.fixture()
def login_as(env):
    return lambda email: Client(email)


@pytest.fixture()
def admin(login_as):
    return login_as("admin@ars.local")


@pytest.fixture()
def superuser(login_as):
    return login_as("super@test.local")


@pytest.fixture()
def recruiter(login_as):
    return login_as(USERS["recruiter"][1])


@pytest.fixture()
def interviewer(login_as):
    return login_as(USERS["interviewer"][1])


@pytest.fixture()
def candidate(login_as):
    return login_as(USERS["candidate"][1])


@pytest.fixture()
def sample(env, admin):
    """One company, position, candidate, application and interview."""
    admin.post("/operations/add/companies", name="Acme")
    admin.post("/jobs/add", title="Python Developer", description="Build APIs", requirements="Python")
    admin.post("/candidates/add", name="Ann Applicant", email="ann@test.local")
    con = db()
    job = con.execute("SELECT id FROM jobs").fetchone()["id"]
    cand = con.execute("SELECT id FROM candidates").fetchone()["id"]
    con.close()
    admin.post("/applications/add", candidate_id=cand, job_id=job)
    con = db()
    app_id = con.execute("SELECT id FROM applications").fetchone()["id"]
    con.close()
    admin.post("/interviews/add", application_id=app_id, round_name="Round 1",
               scheduled_at="2026-10-13T10:31", mode="Online",
               interviewer_user_id=user_id(USERS["interviewer"][1]), meeting_provider="Microsoft Teams", meeting_link="https://teams.microsoft.com/l/meetup-join/abc")
    con = db()
    interview = con.execute("SELECT id FROM interviews").fetchone()["id"]
    con.close()
    return {"job": job, "candidate": cand, "application": app_id, "interview": interview}
