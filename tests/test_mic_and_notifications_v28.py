"""v28: always-on mic button in the top bar, and a Clear button for notifications."""
from recruiter_ats.database import db

HDR = {"X-CSRF-Token": "test-token"}


def _seed_audit(n=3):
    con = db()
    for i in range(n):
        con.execute("INSERT INTO audit_log(action,entity,details,created_at) VALUES(?,?,?,?)",
                    ("CANDIDATE_ADDED", "CANDIDATE", f"Added candidate {i}", "2026-10-10 10:00"))
    con.commit(); con.close()


def test_separate_mic_button_and_clear_button_render(env, recruiter):
    html = recruiter.get("/dashboard").get_data(as_text=True)
    assert 'id="vaLiveMic"' in html            # separate mic button in the top bar
    assert 'id="notificationClear"' in html    # Clear all on the notification panel
    assert "va-mic-state" in html


def test_notifications_can_be_cleared_one_by_one_and_all(env, recruiter):
    _seed_audit(3)
    items = recruiter.get("/api/notifications").get_json()["items"]
    assert len(items) == 3
    one = items[0]["id"]
    assert recruiter.http.post("/api/notifications/clear", json={"id": one}, headers=HDR).get_json()["ok"]
    left = recruiter.get("/api/notifications").get_json()
    assert left["count"] == 2 and one not in [i["id"] for i in left["items"]]
    assert recruiter.http.post("/api/notifications/clear", json={}, headers=HDR).get_json()["ok"]
    assert recruiter.get("/api/notifications").get_json()["count"] == 0
    _seed_audit(1)                               # new activity after clearing shows up again
    assert recruiter.get("/api/notifications").get_json()["count"] == 1


def test_clear_needs_csrf_and_is_per_user(env, recruiter):
    _seed_audit(2)
    assert recruiter.http.post("/api/notifications/clear", json={}).status_code == 400
    assert recruiter.get("/api/notifications").get_json()["count"] == 2
