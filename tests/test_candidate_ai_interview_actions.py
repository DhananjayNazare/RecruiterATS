"""Regression tests for visible candidate AI interview actions and legacy invite repair."""
from datetime import datetime, timedelta

from recruiter_ats.database import db
from recruiter_ats.services.ai_interview import AI_ROUND_NAME


def _selected_candidate_application():
    con = db()
    now = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO jobs(title,description,status,created_at) VALUES('Platform Engineer','Linux and cloud role','Open',?)", (now,))
    jid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO candidates(name,email,resume_text,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('Cara Candidate', 'candidate@test.local', 'Linux, Python, cloud operations', now, now))
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO applications(candidate_id,job_id,status,source,created_at,updated_at) VALUES(?,?,?,'Test',?,?)",
                (cid, jid, 'Interview', now, now))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return cid, aid


def test_candidate_portal_repairs_missing_ai_invite_and_shows_set_time_button(env, login_as):
    candidate = login_as("candidate@test.local")
    cid, aid = _selected_candidate_application()
    response = candidate.get('/candidate-panel')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Set Interview Time' in html

    con = db()
    invite = con.execute("SELECT id,status FROM interviews WHERE application_id=? AND round_name=?",
                         (aid, AI_ROUND_NAME)).fetchone()
    linked = con.execute("SELECT candidate_id FROM users WHERE email='candidate@test.local'").fetchone()
    con.close()
    assert invite is not None and invite['status'] == 'Awaiting Candidate'
    assert linked['candidate_id'] == cid


def test_candidate_can_schedule_and_sees_take_ai_interview_button(env, login_as):
    candidate = login_as("candidate@test.local")
    cid, aid = _selected_candidate_application()
    first = candidate.get('/candidate-panel')
    assert first.status_code == 200
    con = db()
    invite = con.execute("SELECT id FROM interviews WHERE application_id=? AND round_name=?",
                         (aid, AI_ROUND_NAME)).fetchone()
    con.close()
    selected = (datetime.now() + timedelta(minutes=10)).replace(second=0, microsecond=0).isoformat(timespec='minutes')
    response = candidate.post(f"/candidate-panel/ai-interview/{invite['id']}/schedule", scheduled_at=selected)
    assert response.status_code == 302
    portal = candidate.get('/candidate-panel')
    assert portal.status_code == 200
    assert 'Take AI Interview' in portal.get_data(as_text=True)

    con = db()
    row = con.execute("SELECT scheduled_at,status FROM interviews WHERE id=?", (invite['id'],)).fetchone()
    con.close()
    assert row['status'] == 'Scheduled' and row['scheduled_at'] == selected
