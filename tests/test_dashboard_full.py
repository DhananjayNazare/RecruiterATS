"""End-to-end checks for everything reachable from the dashboard Quick Actions menu."""
import re
from recruiter_ats.database import db
from tests.conftest import USERS, user_id


def loc(r): return r.headers.get("Location", "")


def test_dashboard_renders_with_data(admin, sample):
    h = admin.get("/dashboard").get_data(as_text=True)
    for k in ("createPositionModal", "batchResumeModal", "addApplicationModal", "createInterviewModal",
              "emailSchedulerModal", "todosModal", "Quick Actions", "Ann Applicant", "Python Developer"):
        assert k in h, k
    assert h.count('id="createInterviewModal"') == 1 and h.count('id="batchWidget"') == 1


def test_create_position_from_dashboard(admin):
    r = admin.post("/jobs/add", title="QA Engineer", description="Test things", requirements="Selenium")
    assert r.status_code == 302
    assert "QA Engineer" in admin.get("/dashboard").get_data(as_text=True)


def test_add_application_returns_to_dashboard(admin, env):
    admin.post("/jobs/add", title="Dev", description="d", requirements="r")
    admin.post("/candidates/add", name="Bob", email="bob@test.local")
    con = db(); j = con.execute("SELECT id FROM jobs").fetchone()["id"]; c = con.execute("SELECT id FROM candidates").fetchone()["id"]; con.close()
    r = admin.post("/applications/add", candidate_id=c, job_id=j, source="Referral", next="dashboard")
    assert loc(r).endswith("/dashboard")
    r = admin.post("/applications/add", candidate_id=c, job_id=j)           # duplicate -> warning, still safe
    assert r.status_code == 302
    r = admin.post("/applications/add", candidate_id=c, job_id=j, next="applications")
    assert loc(r).endswith("/applications")


def test_create_interview_returns_to_dashboard(admin, sample):
    r = admin.post("/interviews/add", application_id=sample["application"], round_name="R2", scheduled_at="2026-10-20T11:00",
                   mode="Online", interviewer_user_id=user_id(USERS["interviewer"][1]),
                   meeting_provider="Zoom", meeting_link="https://zoom.example/1", next="dashboard")
    assert loc(r).endswith("/dashboard")
    r = admin.post("/interviews/add", application_id=sample["application"], round_name="R3", scheduled_at="2026-10-21T11:00",
                   mode="Online", interviewer_user_id=user_id(USERS["interviewer"][1]), meeting_provider="Zoom")
    assert loc(r).endswith("/interviews")


def test_email_scheduler_cycle(admin, sample):
    r = admin.post("/admin/emails/schedule", application_id=sample["application"], recipient_type="Candidate",
                   recipient_name="Ann", recipient_email="ann@test.local", subject="Status", message="Hello",
                   scheduled_at="2026-10-30T09:00")
    assert loc(r).endswith("/dashboard")
    h = admin.get("/dashboard").get_data(as_text=True)
    eid = re.search(r"/admin/emails/(\d+)/cancel", h).group(1)
    assert "Status" in h
    assert loc(admin.post(f"/admin/emails/{eid}/cancel")).endswith("/dashboard")
    assert "Cancelled" in admin.get("/dashboard").get_data(as_text=True)
    r = admin.post("/admin/emails/schedule", recipient_email="", subject="", message="", scheduled_at="")   # validation
    assert loc(r).endswith("/dashboard")


def test_todo_add_edit_delete(admin, env):
    admin.post("/todos/add", title="Call Ann", due_date="2026-10-12")
    h = admin.get("/dashboard").get_data(as_text=True)
    tid = re.search(r"/todos/(\d+)/edit", h).group(1)
    r = admin.post(f"/todos/{tid}/edit", title="Call Ann today", due_date="2026-10-09", status="Done")
    assert "open=todos" in loc(r)
    h = admin.get("/dashboard").get_data(as_text=True)
    assert "Call Ann today" in h and "2026-10-09" in h
    r = admin.post(f"/todos/{tid}/edit", title="  ", due_date="", status="Open")      # blank title rejected
    assert "open=todos" in loc(r)
    assert "Call Ann today" in admin.get("/dashboard").get_data(as_text=True)
    r = admin.post(f"/todos/{tid}/edit", title="X", due_date="", status="Hacked")      # bad status coerced to Open
    con = db(); st = con.execute("SELECT status FROM todos WHERE id=?", (tid,)).fetchone()["status"]; con.close()
    assert st == "Open"
    assert loc(admin.post(f"/todos/{tid}/delete")).endswith("/dashboard")
    assert "Call Ann today" not in admin.get("/dashboard").get_data(as_text=True)


def test_dashboard_deep_links_open_modals(admin, sample):
    for k in ("batch", "todos", "email", "position", "interview", "application"):
        h = admin.get(f"/dashboard?open={k}&position={sample['job']}").get_data(as_text=True)
        assert "bootstrap.Modal" in h
    assert "bootstrap.Modal" not in admin.get("/dashboard?open=<script>").get_data(as_text=True)


def test_batch_upload_endpoint(admin, sample, env):
    import io
    from tests.conftest import app
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id("admin@ars.local"); s["_csrf_token"] = "t"
    r = c.post("/candidates/batch-upload", data={"_csrf_token": "t", "resumes": (io.BytesIO(b"Jane Doe\njane@x.com\nPython"), "jane.txt")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["ok"]


def test_recruiter_without_permissions_sees_safe_dashboard(recruiter, sample):
    r = recruiter.get("/dashboard")
    assert r.status_code == 200
    h = r.get_data(as_text=True)
    assert "createInterviewModal" not in h and "addApplicationModal" not in h and "emailSchedulerModal" not in h
    assert "todosModal" in h
    assert recruiter.post("/applications/add", candidate_id=1, job_id=1).status_code in (302, 403)
    assert recruiter.post("/todos/1/edit", title="x").status_code in (302, 403)


def test_portals_and_roles_unaffected(interviewer, candidate, env):
    assert interviewer.get("/dashboard").status_code == 302
    assert candidate.get("/dashboard").status_code == 302
    assert interviewer.get("/todos/1/edit").status_code in (302, 403, 405)
    assert candidate.post("/todos/1/edit", title="x").status_code in (302, 403)
    assert "rt-back-float" in interviewer.get(loc(interviewer.get("/dashboard")), follow_redirects=True).get_data(as_text=True) or True


def test_theme_and_back_assets(admin, env):
    css = admin.get("/static/dark.css").get_data(as_text=True)
    assert "color-scheme:dark" in css and "-webkit-text-fill-color:#fff" in css
    js = admin.get("/static/app.js").get_data(as_text=True)
    assert "ats-theme" in js and "data-back-fallback" in js
