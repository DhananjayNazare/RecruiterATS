"""Voice-command service: typed voice password, auto-lock, permission checks, confirmations, audit."""
import time
from werkzeug.security import generate_password_hash
from recruiter_ats.database import db
from conftest import USERS, user_id

HDR = {"X-CSRF-Token": "test-token"}
VOICE_PW = "Voice#Pass-2026-xyz!"          # 20 chars, upper/lower/number/symbol
RECRUITER = USERS["recruiter"][1]


def post(c, url, **body):
    r = c.http.post(url, json=body, headers=HDR)
    return r.status_code, (r.get_json(silent=True) or {})


def cmd(c, intent, **params):
    return post(c, "/api/voice-service/command", intent=intent, params=params)[1]


def confirm(c, reply, answer="yes"):
    return post(c, "/api/voice-service/command", confirm_token=reply["confirm"]["token"], answer=answer)[1]


def set_login_password(email, pw):
    con = db(); con.execute("UPDATE users SET password_hash=? WHERE email=?", (generate_password_hash(pw), email)); con.commit(); con.close()


def setup_voice(c, login_pw="Pass@1234", new=VOICE_PW):
    return post(c, "/api/voice-service/setup", login_password=login_pw, new_password=new, confirm_password=new)


def grant(email, *perms):
    con = db()
    for p in perms:
        con.execute("INSERT INTO user_permissions(user_id,permission,granted) VALUES(?,?,1) ON CONFLICT(user_id,permission) DO UPDATE SET granted=1", (user_id(email), p))
    con.commit(); con.close()


def audit_actions():
    con = db(); rows = [r["action"] for r in con.execute("SELECT action FROM audit_log").fetchall()]; con.close(); return rows


def test_voice_password_rules(env, recruiter):
    weak = {"short1!Aa": "16 characters", "nodigitsHere!!Passwordx": "number", "NoSymbolsHere1234Passwd": "special symbol",
            "alllowercase!1234567890": "uppercase", "ALLUPPERCASE!1234567890": "lowercase"}
    for pw, missing in weak.items():
        code, j = setup_voice(recruiter, new=pw)
        assert code == 400 and missing in j["error"], (pw, j)
    assert setup_voice(recruiter, login_pw="wrong")[0] == 403
    code, j = setup_voice(recruiter)
    assert code == 200 and j["unlocked"]
    con = db(); h = con.execute("SELECT voice_pw_hash FROM users WHERE email=?", (RECRUITER,)).fetchone()[0]; con.close()
    assert h and VOICE_PW not in h                                   # stored hashed
    assert setup_voice(recruiter)[0] == 400                          # same as the current voice password


def test_voice_password_must_differ_from_login_password(env, recruiter):
    strong_login = "Login#Pass-2026-abc!"
    set_login_password(RECRUITER, strong_login)
    code, j = setup_voice(recruiter, login_pw=strong_login, new=strong_login)
    assert code == 400 and "different from your login password" in j["error"]
    assert setup_voice(recruiter, login_pw=strong_login)[0] == 200


def test_locked_until_unlocked_and_normal_assistant_still_works(env, recruiter):
    assert recruiter.get("/api/voice-assistant/briefing?part=daily").get_json()["ok"]       # no voice password needed
    code, j = post(recruiter, "/api/voice-service/command", intent="open_page", params={"page": "candidates"})
    assert code == 401 and j["locked"]
    assert post(recruiter, "/api/voice-service/unlock", password=VOICE_PW)[0] == 400        # nothing set yet
    setup_voice(recruiter); post(recruiter, "/api/voice-service/lock")
    assert post(recruiter, "/api/voice-service/command", intent="open_page", params={"page": "candidates"})[0] == 401
    assert post(recruiter, "/api/voice-service/unlock", password="not the password")[0] == 403
    assert post(recruiter, "/api/voice-service/unlock", password=VOICE_PW)[0] == 200
    assert cmd(recruiter, "open_page", page="candidates")["navigate"] == "/candidates"
    assert recruiter.get("/api/voice-service/status").get_json()["unlocked"]


def test_five_wrong_passwords_block_the_service(env, recruiter):
    setup_voice(recruiter); post(recruiter, "/api/voice-service/lock")
    for _ in range(4): assert post(recruiter, "/api/voice-service/unlock", password="nope")[0] == 403
    assert post(recruiter, "/api/voice-service/unlock", password="nope")[0] == 429
    assert post(recruiter, "/api/voice-service/unlock", password=VOICE_PW)[0] == 429         # even the right one is refused while blocked
    assert "VOICE_LOCKOUT" in audit_actions()


def test_auto_lock_when_idle_and_on_logout(env, recruiter):
    setup_voice(recruiter)
    assert cmd(recruiter, "count", what="candidates")["ok"]
    with recruiter.http.session_transaction() as s: s["vs_last"] = time.time() - 301         # idle for more than 5 minutes
    assert post(recruiter, "/api/voice-service/command", intent="count", params={"what": "candidates"})[0] == 401
    assert recruiter.get("/api/voice-service/status").get_json()["unlocked"] is False
    post(recruiter, "/api/voice-service/unlock", password=VOICE_PW)
    assert cmd(recruiter, "count", what="candidates")["ok"]
    recruiter.get("/logout")                                                                  # log off ends the unlock
    with recruiter.http.session_transaction() as s: s["user_id"] = user_id(RECRUITER); s["_csrf_token"] = "test-token"   # same user signs in again
    assert post(recruiter, "/api/voice-service/command", intent="count", params={"what": "candidates"})[0] == 401


def test_permissions_are_enforced_on_the_server(env, recruiter):
    setup_voice(recruiter)
    for intent_, params in (("add_user", {"name": "New Person", "email": "new@test.local"}), ("delete_user", {"name": "Ivan"}),
                            ("add_candidate", {"name": "Pat"}), ("delete_candidate", {"name": "Pat"}), ("add_position", {"title": "QA"}),
                            ("schedule_interview", {"candidate": "Pat", "when": "2099-01-01T10:00"}), ("open_page", {"page": "admin"}), ("open_page", {"page": "reports"})):
        r = cmd(recruiter, intent_, **params)
        assert r["ok"] is False and r.get("denied"), (intent_, r)
    assert "VOICE_DENIED" in audit_actions()
    con = db(); assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0 and not con.execute("SELECT 1 FROM users WHERE email='new@test.local'").fetchone(); con.close()
    grant(RECRUITER, "manage_candidates")
    r = cmd(recruiter, "add_candidate", name="Pat Patel", email="pat@test.local")
    assert r["ok"] and "confirm" in r                                                          # read back + confirm before saving
    con = db(); assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0; con.close()
    assert confirm(recruiter, r)["ok"]
    con = db(); assert con.execute("SELECT email FROM candidates").fetchone()[0] == "pat@test.local"; con.close()
    assert cmd(recruiter, "delete_user", name="Ivan")["denied"]                                # still no user-management permission


def test_add_user_by_voice_needs_permission_and_never_speaks_the_password(env, recruiter, admin):
    set_login_password("admin@ars.local", "Admin#Login-2026-pw!")
    assert setup_voice(admin, login_pw="Admin#Login-2026-pw!")[0] == 200
    r = cmd(admin, "add_user", name="Rahul Sharma", email="rahul@test.local", role="recruiter")
    assert "confirm" in r and "rahul at test dot local" in r["speech"]
    done = confirm(admin, r)
    assert done["ok"] and "screen_note" in done and done["screen_note"].split(": ")[1] not in done["speech"]
    con = db(); row = con.execute("SELECT role,must_change_password,totp_required FROM users WHERE email='rahul@test.local'").fetchone(); con.close()
    assert (row["role"], row["must_change_password"], row["totp_required"]) == ("Recruiter", 1, 1)
    con = db(); details = " ".join(r["details"] or "" for r in con.execute("SELECT details FROM audit_log").fetchall()); con.close()
    assert done["screen_note"].split(": ")[1] not in details
    assert cmd(admin, "add_user", name="Rahul Again", email="rahul@test.local")["ok"] is False   # duplicate email
    # a Manager with user permission can add users, but not Admins
    con = db(); con.execute("UPDATE users SET role='Manager' WHERE email=?", (RECRUITER,)); con.commit(); con.close()
    grant(RECRUITER, "manage_users"); setup_voice(recruiter)
    assert "Only an Admin" in cmd(recruiter, "add_user", name="Boss Person", email="boss@test.local", role="Admin")["speech"]


def test_delete_needs_spoken_yes_and_keeps_the_admin_safeguards(env, recruiter, admin, sample):
    set_login_password("admin@ars.local", "Admin#Login-2026-pw!"); setup_voice(admin, login_pw="Admin#Login-2026-pw!")
    r = cmd(admin, "delete_candidate", name="Ann")
    assert "confirm" in r
    assert confirm(admin, r, "no")["speech"] == "Okay, cancelled."
    con = db(); assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1; con.close()
    bad = post(admin, "/api/voice-service/command", confirm_token="wrong", answer="yes")[1]
    assert bad["ok"] is False                                                                  # token must match the pending question
    r = cmd(admin, "delete_candidate", name="Ann")
    assert confirm(admin, r)["ok"]
    con = db(); assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0; con.close()
    assert "cannot delete your own" in cmd(admin, "delete_user", name="Administrator")["speech"]
    r = cmd(admin, "delete_user", name="Rita")
    assert confirm(admin, r)["ok"]
    con = db(); assert not con.execute("SELECT 1 FROM users WHERE email=?", (RECRUITER,)).fetchone(); con.close()
    # an expired confirmation does nothing
    r = cmd(admin, "delete_user", name="Ivan")
    with admin.http.session_transaction() as s:
        pending = dict(s["vs_confirm"]); pending["exp"] = time.time() - 1; s["vs_confirm"] = pending
    assert "expired" in confirm(admin, r)["speech"]


def test_status_interview_search_count_and_screening_flow(env, admin, sample):
    set_login_password("admin@ars.local", "Admin#Login-2026-pw!"); setup_voice(admin, login_pw="Admin#Login-2026-pw!")
    assert cmd(admin, "update_status", candidate="Ann", status="shortlisted")["ok"]
    con = db(); assert con.execute("SELECT status FROM applications").fetchone()[0] == "Shortlisted"; con.close()
    r = cmd(admin, "update_status", candidate="Ann", status="rejected")
    assert "confirm" in r and confirm(admin, r)["ok"]                                          # Rejected needs a yes
    assert cmd(admin, "update_status", candidate="Nobody", status="hired")["ok"] is False
    r = cmd(admin, "schedule_interview", candidate="Ann", when="2099-03-04T11:00")
    assert "confirm" in r and confirm(admin, r)["ok"]
    r = cmd(admin, "cancel_interview", candidate="Ann", job="python")
    assert "2 scheduled" in r["speech"] or "confirm" in r or r["ok"] is False
    assert "already passed" in cmd(admin, "schedule_interview", candidate="Ann", when="2020-01-01T10:00")["speech"]
    assert "Ann Applicant" in cmd(admin, "search", q="ann")["speech"]
    assert cmd(admin, "count", what="candidates")["speech"] == "You have 1 candidate."
    assert cmd(admin, "add_position", title="QA Engineer")["ok"]
    assert cmd(admin, "open_page", page="reports")["navigate"] == "/reports"
    r = cmd(admin, "screen", candidate="Ann")
    assert "confirm" in r                                                                      # AI screening also asks first


def test_tasks_by_voice(env, admin):
    set_login_password("admin@ars.local", "Admin#Login-2026-pw!"); setup_voice(admin, login_pw="Admin#Login-2026-pw!")
    con = db(); con.execute("INSERT INTO todos(title,created_at) VALUES('Call Raj','2026-10-10')"); con.commit(); con.close()
    assert cmd(admin, "complete_task", title="raj")["ok"]
    r = cmd(admin, "delete_task", title="call raj")
    assert confirm(admin, r)["ok"]
    con = db(); assert con.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 0; con.close()


def test_admin_can_reset_a_voice_password_and_logins_cannot_reuse_it(env, admin, recruiter):
    setup_voice(recruiter)
    uid = user_id(RECRUITER)
    assert admin.post(f"/admin/users/{uid}/edit", name="Rita Recruiter", email=RECRUITER, role="Recruiter", password=VOICE_PW).get_data(as_text=True) is not None
    con = db(); assert con.execute("SELECT voice_pw_hash FROM users WHERE id=?", (uid,)).fetchone()[0]; con.close()   # login pw == voice pw was refused, hash intact
    con = db(); same = con.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()[0]; con.close()
    assert not __import__("werkzeug.security", fromlist=["x"]).check_password_hash(same, VOICE_PW)
    admin.post(f"/admin/users/{uid}/voice-reset")
    con = db(); assert con.execute("SELECT voice_pw_hash FROM users WHERE id=?", (uid,)).fetchone()[0] is None; con.close()
    assert recruiter.get("/api/voice-service/status").get_json()["has_password"] is False
    assert "VOICE_PASSWORD_RESET" in audit_actions()


def test_portal_accounts_cannot_reach_the_voice_service(env, interviewer, candidate):
    for c in (interviewer, candidate):
        assert c.get("/api/voice-service/status").status_code in (302, 403)
        assert c.http.post("/api/voice-service/command", json={"intent": "count"}, headers=HDR).status_code in (302, 403)


def test_csrf_is_required(env, recruiter):
    assert recruiter.http.post("/api/voice-service/setup", json={"login_password": "Pass@1234", "new_password": VOICE_PW}).status_code == 400


def test_free_form_interpret_only_translates(env, recruiter, monkeypatch):
    """AI output is filtered to known intents; destructive ones still need a spoken yes."""
    from recruiter_ats.services import ai
    fake = {"steps": [{"intent": "drop_database", "params": {}},
                      {"intent": "count", "params": {"what": "candidates"}},
                      {"intent": "reminder", "params": {"text": "call Priya", "at": "2030-01-01T10:00"}}],
            "reply": ""}
    monkeypatch.setattr(ai, "ai_json", lambda prompt: (fake, None))
    code, j = post(recruiter, "/api/voice-service/interpret", text="how many candidates and remind me to call Priya")
    assert code == 200 and j["ok"]
    assert [s["intent"] for s in j["steps"]] == ["count", "reminder"]       # unknown intent dropped
    monkeypatch.setattr(ai, "ai_json", lambda prompt: (None, "OPENAI_API_KEY is not configured."))
    assert post(recruiter, "/api/voice-service/interpret", text="do something clever")[1]["unavailable"] is True
