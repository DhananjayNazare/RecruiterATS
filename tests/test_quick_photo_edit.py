"""Quick Actions on every page, JD popup closes after save, candidate photo upload, interview edit."""
import io
from PIL import Image
from recruiter_ats.database import db

def _seed(n=1):
    con = db(); now = "2026-10-10T10:00:00"
    con.execute("INSERT INTO jobs(title,description,status,created_at) VALUES('Tester','JD text','Open',?)", (now,))
    jid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO candidates(name,email,created_at) VALUES('Asha Rao','asha@x.test',?)", (now,))
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO applications(candidate_id,job_id,status,created_at) VALUES(?,?,'Interview',?)", (cid, jid, now))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO interviews(application_id,round_name,scheduled_at,mode,interviewer,status) VALUES(?,?,?,?,?,?)",
                (aid, "Round 1", "2026-10-12T10:00", "Online", "Old Person", "Scheduled"))
    iid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit(); con.close()
    return jid, cid, iid

def test_quick_actions_on_pages(env, admin):
    for url in ("/candidates", "/jobs", "/interviews", "/applications", "/reports"):
        assert b'id="quickActions"' in admin.get(url).data, url
    assert b'id="quickActions"' not in admin.get("/dashboard").data   # dashboard already has its own quick actions

def test_jd_save_closes_popup(env, admin):
    jid, _, _ = _seed()
    r = admin.post(f"/positions/{jid}/jd", title="Tester", status="Open", description="New JD", requirements="x")
    assert r.status_code == 302 and "open=" not in r.headers["Location"]

def test_candidate_photo_upload_and_remove(env, admin):
    _, cid, _ = _seed()
    buf = io.BytesIO(); Image.new("RGB", (40, 40), "red").save(buf, "PNG"); buf.seek(0)
    r = admin.http.post(f"/candidates/{cid}/photo", data={"_csrf_token": "test-token", "photo": (buf, "me.png")}, content_type="multipart/form-data")
    assert r.status_code == 302
    con = db(); photo = con.execute("SELECT photo FROM candidates WHERE id=?", (cid,)).fetchone()[0]; con.close()
    assert photo.endswith("_candidate.jpg")
    assert b"Change photo" in admin.get(f"/candidates/{cid}").data
    admin.post(f"/candidates/{cid}/photo", remove_photo="1")
    con = db(); assert not con.execute("SELECT photo FROM candidates WHERE id=?", (cid,)).fetchone()[0]; con.close()

def test_calendar_view_candidate_and_edit_interview(env, admin):
    _, cid, iid = _seed()
    page = admin.get("/interviews").data
    assert f"cid:{cid}".encode() in page and b"editInterviewModal" in page
    r = admin.post(f"/interviews/{iid}/edit", scheduled_at="2026-10-15T14:30", interviewer="New Person", round_name="Final", mode="Phone", status="Scheduled")
    assert r.status_code == 302
    con = db(); row = con.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone(); con.close()
    assert row["scheduled_at"] == "2026-10-15T14:30" and row["interviewer"] == "New Person" and row["mode"] == "Phone" and row["round_name"] == "Final"
    admin.post(f"/interviews/{iid}/edit", scheduled_at="not-a-date", interviewer="Zed")
    con = db(); assert con.execute("SELECT interviewer FROM interviews WHERE id=?", (iid,)).fetchone()[0] == "New Person"; con.close()
