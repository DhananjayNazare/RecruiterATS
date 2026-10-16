"""Voice assistant: briefing is built from the local DB only (no AI call) and tasks can be added."""
from datetime import datetime, timedelta
from recruiter_ats.database import db
from recruiter_ats.services import assistant

def test_speech_offline_no_ai(env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    now = datetime.now()
    con = db()
    con.execute("INSERT INTO todos(title,due_date,created_at) VALUES('Call Raj',?, 'x')", (now.date().isoformat(),))
    con.execute("INSERT INTO todos(title,due_date,created_at) VALUES('Old task',?, 'x')", ((now - timedelta(days=2)).date().isoformat(),))
    con.execute("INSERT INTO todos(title,created_at) VALUES('Someday','x')")
    con.commit(); con.close()
    data = assistant.collect(now)
    text = assistant.speech(data, "Rita Recruiter")
    assert "Rita" in text and "Call Raj" in text and "overdue" in text and "no interviews today" in text
    assert any(r["key"].startswith("td") for r in assistant.reminders(data))
    assert "Custom tasks" in assistant.speech(data, part="custom")


def test_api_requires_staff_and_adds_task(env, recruiter, candidate):
    assert candidate.get("/api/voice-assistant/briefing").status_code == 302
    r = recruiter.get("/api/voice-assistant/briefing?part=all")
    assert r.status_code == 200 and r.get_json()["ok"]
    hdr = {"X-CSRF-Token": "test-token"}
    r = recruiter.http.post("/api/voice-assistant/task", json={"title": "Send offer", "due_date": "2026-10-09"}, headers=hdr)
    assert r.get_json()["ok"]
    assert "Send offer" in recruiter.get("/api/voice-assistant/briefing?part=custom").get_json()["speech"]
    assert recruiter.http.post("/api/voice-assistant/task", json={"title": " "}, headers=hdr).status_code == 400


def test_widget_in_staff_shell_only(env, recruiter, candidate):
    assert "vaWrap" in recruiter.get("/dashboard").get_data(as_text=True)
    assert "voice_assistant.js" in recruiter.get("/dashboard").get_data(as_text=True)
    assert "vaWrap" not in candidate.get("/candidate-portal", follow_redirects=True).get_data(as_text=True)


def test_interviews_part_removed_and_reminders_part_added(env, recruiter):
    # "Daily tasks" already covers today's interviews, so the duplicate "interviews" briefing is gone.
    hdr = {"X-CSRF-Token": "test-token"}
    d = recruiter.get("/api/voice-assistant/briefing?part=interviews").get_json()
    assert d["ok"] and "Reminders" not in d["speech"]          # unknown part falls back to the full summary
    assert "Daily tasks" in recruiter.get("/api/voice-assistant/briefing?part=daily").get_json()["speech"]
    assert "no reminders set" in recruiter.get("/api/voice-assistant/briefing?part=reminders").get_json()["speech"]
    html = recruiter.get("/dashboard").get_data(as_text=True)
    assert 'data-va="interviews"' not in html and 'data-va="reminders"' in html


def test_user_reminders_are_saved_listed_spoken_and_private(env, recruiter, interviewer):
    from datetime import datetime, timedelta
    hdr = {"X-CSRF-Token": "test-token"}
    when = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    r = recruiter.http.post("/api/voice-assistant/reminders", json={"text": "Send the offer to Priya", "at": when}, headers=hdr)
    j = r.get_json(); assert r.status_code == 200 and j["ok"]
    rid = j["reminder"]["id"]
    assert [x["text"] for x in recruiter.get("/api/voice-assistant/reminders").get_json()["reminders"]] == ["Send the offer to Priya"]
    assert "Send the offer to Priya" in recruiter.get("/api/voice-assistant/briefing?part=reminders").get_json()["speech"]
    # validation
    assert recruiter.http.post("/api/voice-assistant/reminders", json={"text": " ", "at": when}, headers=hdr).status_code == 400
    assert recruiter.http.post("/api/voice-assistant/reminders", json={"text": "x", "at": "nonsense"}, headers=hdr).status_code == 400
    past = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    assert recruiter.http.post("/api/voice-assistant/reminders", json={"text": "x", "at": past}, headers=hdr).status_code == 400
    # POST needs the CSRF token
    assert recruiter.http.post("/api/voice-assistant/reminders", json={"text": "x", "at": when}).status_code == 400
    # done / delete only touch your own reminders
    assert interviewer.get("/api/voice-assistant/reminders").status_code == 302
    assert recruiter.http.post(f"/api/voice-assistant/reminders/{rid}/done", json={}, headers=hdr).get_json()["ok"]
    assert recruiter.get("/api/voice-assistant/reminders").get_json()["reminders"] == []
    j = recruiter.http.post("/api/voice-assistant/reminders", json={"text": "Call Raj", "at": when}, headers=hdr).get_json()
    assert recruiter.http.post(f"/api/voice-assistant/reminders/{j['reminder']['id']}/delete", json={}, headers=hdr).get_json()["ok"]
    assert recruiter.get("/api/voice-assistant/reminders").get_json()["reminders"] == []
