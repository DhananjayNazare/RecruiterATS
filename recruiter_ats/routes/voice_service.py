"""Voice-command service.

Separate from the always-available briefing assistant (routes/assistant.py). Everything here is locked behind a
*voice password* that is typed (never spoken), differs from the login password and is strong. The unlocked state lives
only in the signed server session: it ends after an idle period (ARS_VOICE_IDLE_SECONDS, default 5 minutes), on "lock",
and whenever the user logs off (logout clears the whole session). Each action re-checks the user's permission on the
server, destructive or sensitive actions need a spoken "yes", and every action is written to the audit log.
"""
import re
import secrets
import string
import time
from datetime import datetime, timedelta

from flask import jsonify, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from recruiter_ats import config
from recruiter_ats.config import PERMISSIONS, app
from recruiter_ats.database import db
from recruiter_ats.security import audit, current_user, has_permission, staff_only

MAX_FAILURES = 5
LOCK_MINUTES = 15
CONFIRM_SECONDS = 60
STATUSES = ["Applied", "Screening", "Accepted", "Hold", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]
CONFIRM_STATUSES = {"Rejected", "Hired"}
ASSIGNABLE_ROLES = ["Recruiter", "Manager", "Interviewer", "Admin"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
VIA = "via voice assistant"


# --------------------------------------------------------------------------- password + lock state
def password_problems(pw):
    """Return what is missing from a proposed voice password (empty list = strong enough)."""
    out = []
    if len(pw) < 16: out.append("at least 16 characters")
    if not re.search(r"\d", pw): out.append("a number")
    if not any(ch in string.punctuation for ch in pw): out.append("a special symbol (for example ! @ # $ %)")
    if not re.search(r"[A-Z]", pw): out.append("an uppercase letter")
    if not re.search(r"[a-z]", pw): out.append("a lowercase letter")
    return out


def _now(): return time.time()
def _unlocked(u): return bool(u) and session.get("vs_uid") == u["id"] and _now() - session.get("vs_last", 0) <= config.VOICE_IDLE_SECONDS
def _touch(): session["vs_last"] = _now()
def _lock():
    for k in ("vs_uid", "vs_last", "vs_confirm"): session.pop(k, None)
def _unlock(u): session["vs_uid"] = u["id"]; _touch()


def _lock_minutes_left(u):
    try: until = datetime.fromisoformat(u["voice_locked_until"]) if u["voice_locked_until"] else None
    except ValueError: until = None
    if until and until > datetime.now():
        return max(1, int((until - datetime.now()).total_seconds() // 60) + 1)
    return 0


def _register_failure(u, what):
    """Count a wrong password; after MAX_FAILURES the voice service is blocked for LOCK_MINUTES."""
    n = (u["voice_failed"] or 0) + 1
    until = None
    if n >= MAX_FAILURES:
        until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds"); n = 0
    con = db(); con.execute("UPDATE users SET voice_failed=?, voice_locked_until=COALESCE(?,voice_locked_until) WHERE id=?", (n, until, u["id"])); con.commit(); con.close()
    audit("VOICE_LOCKOUT" if until else "VOICE_AUTH_FAILED", "USER", u["id"], what, actor_id=u["id"])
    return bool(until)


def _clear_failures(u):
    con = db(); con.execute("UPDATE users SET voice_failed=0, voice_locked_until=NULL WHERE id=?", (u["id"],)); con.commit(); con.close()


def _blocked_response(u):
    mins = _lock_minutes_left(u)
    return jsonify(ok=False, error=f"Too many wrong passwords. Voice commands are blocked for about {mins} more minute{'s' if mins != 1 else ''}."), 429


# --------------------------------------------------------------------------- password / lock endpoints
@app.route("/api/voice-service/status")
@staff_only
def vs_status():
    u = current_user()
    on = _unlocked(u)
    if not on and session.get("vs_uid"): _lock()
    left = max(0, int(config.VOICE_IDLE_SECONDS - (_now() - session.get("vs_last", 0)))) if on else 0
    return jsonify(ok=True, has_password=bool(u["voice_pw_hash"]), unlocked=on, seconds_left=left,
                   blocked_minutes=_lock_minutes_left(u), idle_seconds=config.VOICE_IDLE_SECONDS)


@app.route("/api/voice-service/setup", methods=["POST"])
@staff_only
def vs_setup():
    """Create or change the voice password. Needs the login password, so an unattended screen cannot set one."""
    u = current_user(); d = request.get_json(silent=True) or {}
    if _lock_minutes_left(u): return _blocked_response(u)
    login_pw, new, confirm = d.get("login_password") or "", d.get("new_password") or "", d.get("confirm_password")
    if not check_password_hash(u["password_hash"], login_pw):
        if _register_failure(u, "Wrong login password while setting the voice password"): return _blocked_response(u)
        return jsonify(ok=False, error="Your login password is not correct."), 403
    problems = password_problems(new)
    if problems: return jsonify(ok=False, error="The voice password needs " + ", ".join(problems) + ".", missing=problems), 400
    if confirm is not None and confirm != new: return jsonify(ok=False, error="The two voice passwords do not match."), 400
    if check_password_hash(u["password_hash"], new):
        return jsonify(ok=False, error="The voice password must be different from your login password."), 400
    if u["voice_pw_hash"] and check_password_hash(u["voice_pw_hash"], new):
        return jsonify(ok=False, error="Choose a different password from your current voice password."), 400
    con = db()
    con.execute("UPDATE users SET voice_pw_hash=?, voice_pw_changed_at=?, voice_failed=0, voice_locked_until=NULL WHERE id=?",
                (generate_password_hash(new), datetime.now().isoformat(timespec="seconds"), u["id"]))
    con.commit(); con.close()
    audit("VOICE_PASSWORD_SET", "USER", u["id"], "Voice-command password created or changed")
    _unlock(u)
    return jsonify(ok=True, unlocked=True, idle_seconds=config.VOICE_IDLE_SECONDS)


@app.route("/api/voice-service/unlock", methods=["POST"])
@staff_only
def vs_unlock():
    u = current_user(); d = request.get_json(silent=True) or {}
    if not u["voice_pw_hash"]: return jsonify(ok=False, error="Create your voice password first.", needs_setup=True), 400
    if _lock_minutes_left(u): return _blocked_response(u)
    if not check_password_hash(u["voice_pw_hash"], d.get("password") or ""):
        if _register_failure(u, "Wrong voice password"): return _blocked_response(u)
        return jsonify(ok=False, error="That voice password is not correct."), 403
    _clear_failures(u); _unlock(u)
    audit("VOICE_UNLOCKED", "USER", u["id"], "Voice commands unlocked")
    return jsonify(ok=True, unlocked=True, idle_seconds=config.VOICE_IDLE_SECONDS)


@app.route("/api/voice-service/lock", methods=["POST"])
@staff_only
def vs_lock():
    was = _unlocked(current_user()); _lock()
    if was: audit("VOICE_LOCKED", "USER", None, "Voice commands locked")
    return jsonify(ok=True, unlocked=False)


# --------------------------------------------------------------------------- command plumbing
HANDLERS = {}
def intent(name):
    def deco(fn): HANDLERS[name] = fn; return fn
    return deco

def say(text, **extra): return {"ok": True, "speech": text, **extra}
def fail(text, **extra): return {"ok": False, "speech": text, **extra}
def _clean(v): return re.sub(r"\s+", " ", str(v or "")).strip()
def _like(v): return "%" + re.sub(r"[%_]", "", _clean(v).lower()) + "%"
def _plural(n, w): return f"{n} {w}{'' if n == 1 else 's'}"
def _when_text(iso):
    try: return datetime.strptime(iso, "%Y-%m-%dT%H:%M").strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
    except ValueError: return iso


def need(perm):
    """None when the signed-in user holds `perm` (Admins always do); otherwise a refusal that is also audit-logged."""
    if has_permission(perm): return None
    label = next((lbl for key, lbl, _ in PERMISSIONS if key == perm), perm)
    audit("VOICE_DENIED", "VOICE", None, f"Missing permission: {label}")
    return fail(f"You don't have the {label} permission, so I can't do that. Ask your administrator to grant it.", denied=True)


def ask(name, params, prompt):
    token = secrets.token_urlsafe(8)
    session["vs_confirm"] = {"token": token, "intent": name, "params": params, "exp": _now() + CONFIRM_SECONDS, "uid": session.get("user_id")}
    text = f"{prompt} Say yes to confirm, or no to cancel."
    return {"ok": True, "speech": text, "confirm": {"token": token, "prompt": text}}


@app.route("/api/voice-service/command", methods=["POST"])
@staff_only
def vs_command():
    u = current_user(); d = request.get_json(silent=True) or {}
    if not _unlocked(u):
        _lock()
        return jsonify(ok=False, locked=True, speech="Voice commands are locked. Type your voice password to unlock them.",
                       error="Voice commands are locked."), 401
    _touch()
    if d.get("confirm_token"):                                   # answer to "say yes to confirm"
        pending = session.pop("vs_confirm", None)
        if not pending or pending["token"] != d["confirm_token"] or pending["uid"] != u["id"] or pending["exp"] < _now():
            return jsonify(fail("That confirmation has expired. Please repeat the command."))
        if str(d.get("answer", "")).lower() not in ("yes", "y", "confirm"):
            return jsonify(say("Okay, cancelled."))
        fn = HANDLERS.get(pending["intent"])
        return jsonify(fn(u, pending["params"], True) if fn else fail("Sorry, that command is no longer available."))
    session.pop("vs_confirm", None)
    fn = HANDLERS.get(d.get("intent"))
    if not fn: return jsonify(fail("Sorry, I do not know that voice command."))
    params = d.get("params") if isinstance(d.get("params"), dict) else {}
    try:
        return jsonify(fn(u, params, False))
    except Exception as exc:                                     # never leak internals into speech
        app.logger.exception("Voice command %s failed: %s", d.get("intent"), exc)
        return jsonify(fail("Sorry, something went wrong and nothing was changed."))


# --------------------------------------------------------------------------- lookups
def _pick(rows, what, label, hint):
    if not rows: return None, fail(f"I could not find {what}.")
    if len(rows) == 1: return rows[0], None
    return None, fail(f"I found {len(rows)} matches: {', '.join(label(r) for r in rows[:5])}. {hint}", ambiguous=True)


def _candidate(con, text):
    t = _clean(text)
    if not t: return None, fail("Tell me the candidate's name.")
    rows = con.execute("SELECT id,name,email FROM candidates WHERE lower(name)=lower(?) ORDER BY id", (t,)).fetchall() \
        or con.execute("SELECT id,name,email FROM candidates WHERE lower(name) LIKE ? OR lower(email) LIKE ? ORDER BY name LIMIT 6", (_like(t), _like(t))).fetchall()
    return _pick(rows, f"a candidate called {t}", lambda r: r["name"], "Say the full name and try again.")


def _application(con, cand, job_text):
    rows = con.execute("SELECT a.id,a.status,j.title FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? ORDER BY a.id", (cand["id"],)).fetchall()
    if job_text: rows = [r for r in rows if _clean(job_text).lower() in r["title"].lower()]
    if not rows: return None, fail(f"{cand['name']} has no application" + (f" for {_clean(job_text)}." if job_text else "."))
    return _pick(rows, "an application", lambda r: r["title"], f"Add the position name, for example: for {rows[0]['title']}.") if len(rows) > 1 else (rows[0], None)


# --------------------------------------------------------------------------- navigation / questions
PAGES = {  # spoken name -> (endpoint, permission or None, label)
    "dashboard": ("dashboard", None, "the dashboard"), "home": ("dashboard", None, "the dashboard"),
    "candidates": ("candidates", None, "candidates"), "positions": ("jobs", None, "positions"), "jobs": ("jobs", None, "positions"),
    "applications": ("applications", None, "applications"), "ai screening": ("ai_center", None, "AI screening"), "ai": ("ai_center", None, "AI screening"),
    "calendar": ("calendar_view", None, "the calendar"), "interviews": ("interviews", None, "interviews"),
    "team": ("operations", None, "the team page"), "meetings": ("integrations", None, "meetings"),
    "reports": ("reports", "view_reports", "reports"), "admin": ("admin", "manage_users", "the admin page"), "settings": ("settings", None, "settings"),
}


@intent("open_page")
def h_open_page(u, p, confirmed):
    entry = PAGES.get(_clean(p.get("page")).lower())
    if not entry: return fail("I do not know that page. Try candidates, positions, applications, calendar, reports, admin or settings.")
    endpoint, perm, label = entry
    if perm and (denied := need(perm)): return denied
    return say(f"Opening {label}.", navigate=url_for(endpoint))


@intent("search")
def h_search(u, p, confirmed):
    q = _clean(p.get("q"))
    if not q: return fail("What should I search for?")
    con = db()
    cands = con.execute("SELECT name FROM candidates WHERE lower(name) LIKE ? OR lower(email) LIKE ? OR lower(COALESCE(skills,'')) LIKE ? ORDER BY name LIMIT 5", (_like(q),) * 3).fetchall()
    jobs = con.execute("SELECT title FROM jobs WHERE lower(title) LIKE ? ORDER BY title LIMIT 3", (_like(q),)).fetchall()
    con.close()
    bits = []
    if cands: bits.append(f"{_plural(len(cands), 'candidate')}: " + ", ".join(r["name"] for r in cands))
    if jobs: bits.append(f"{_plural(len(jobs), 'position')}: " + ", ".join(r["title"] for r in jobs))
    if not bits: return say(f"I found nothing for {q}.")
    return say(f"For {q} I found " + ". And ".join(bits) + ".", navigate=url_for("candidates", q=q) if cands else None)


@intent("count")
def h_count(u, p, confirmed):
    what = _clean(p.get("what")).lower(); con = db()
    sql = {"candidates": ("SELECT COUNT(*) c FROM candidates", "candidate"),
           "applications": ("SELECT COUNT(*) c FROM applications", "application"),
           "positions": ("SELECT COUNT(*) c FROM jobs WHERE status='Open'", "open position"),
           "interviews": (f"SELECT COUNT(*) c FROM interviews WHERE status='Scheduled' AND scheduled_at>='{datetime.now().strftime('%Y-%m-%dT%H:%M')}'", "upcoming interview")}.get(what)
    if not sql: con.close(); return fail("I can count candidates, applications, positions or interviews.")
    n = con.execute(sql[0]).fetchone()["c"]; con.close()
    return say(f"You have {_plural(n, sql[1])}.")


# --------------------------------------------------------------------------- candidates / positions / tasks
@intent("add_candidate")
def h_add_candidate(u, p, confirmed):
    if denied := need("manage_candidates"): return denied
    name, email, phone = _clean(p.get("name")), _clean(p.get("email")).lower(), _clean(p.get("phone"))
    if not name: return fail("Tell me the candidate's name, for example: add candidate Priya Sharma.")
    if email and not EMAIL_RE.match(email): return fail(f"I heard the email as {email}, which does not look valid. Please say it again or type it on the Candidates page.")
    con = db()
    dup = con.execute("SELECT name FROM candidates WHERE lower(email)=? AND ?<>''", (email, email)).fetchone() if email else None
    if dup: con.close(); return fail(f"A candidate with that email already exists: {dup['name']}.")
    if not confirmed:
        con.close()
        return ask("add_candidate", {"name": name, "email": email, "phone": phone},
                   f"Add candidate {name}" + (f", email {email.replace('@', ' at ').replace('.', ' dot ')}" if email else ", with no email") + ".")
    now = datetime.now().isoformat(timespec="seconds")
    cur = con.execute("INSERT INTO candidates(name,email,phone,location,resume_file,resume_text,skills,experience_years,education,photo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (name, email, phone, "", "", "", "", 0, "", "", now, now))
    cid = cur.lastrowid; con.commit(); con.close()
    audit("CREATE", "CANDIDATE", cid, f"{name} {VIA}")
    return say(f"Candidate {name} added.", navigate=url_for("candidate_detail", cid=cid))


@intent("delete_candidate")
def h_delete_candidate(u, p, confirmed):
    if denied := need("manage_candidates"): return denied
    con = db()
    if not confirmed:
        c, err = _candidate(con, p.get("name")); con.close()
        if err: return err
        return ask("delete_candidate", {"id": c["id"], "name": c["name"]}, f"Delete candidate {c['name']} and all of their applications. This cannot be undone.")
    c = con.execute("SELECT * FROM candidates WHERE id=?", (p["id"],)).fetchone()
    if not c: con.close(); return fail("That candidate no longer exists.")
    con.execute("DELETE FROM candidates WHERE id=?", (c["id"],)); con.commit(); con.close()
    for f in (c["resume_file"], c["photo"]):
        if f:
            try: (config.UPLOAD_DIR / f).unlink(missing_ok=True)
            except Exception: pass
    audit("DELETE", "CANDIDATE", c["id"], f"{c['name']} {VIA}")
    return say(f"Candidate {c['name']} deleted.")


@intent("add_position")
def h_add_position(u, p, confirmed):
    if denied := need("manage_jobs"): return denied
    title = _clean(p.get("title"))
    if not title: return fail("Tell me the position title, for example: add position Python Developer.")
    now = datetime.now().isoformat(timespec="seconds")
    con = db()
    cur = con.execute("INSERT INTO jobs(title,description,requirements,employment_type,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (title, "Created by voice assistant. Open this position and add the full job description.", "", "Full-time", "Open", u["id"], now, now))
    jid = cur.lastrowid; con.commit(); con.close()
    audit("CREATE", "JOB", jid, f"{title} {VIA}")
    return say(f"Position {title} created. Add its job description on the Positions page.", navigate=url_for("jobs"))


@intent("delete_position")
def h_delete_position(u, p, confirmed):
    if denied := need("manage_jobs"): return denied
    con = db()
    if not confirmed:
        rows = con.execute("SELECT id,title FROM jobs WHERE lower(title)=lower(?) ORDER BY id", (_clean(p.get("title")),)).fetchall() \
            or con.execute("SELECT id,title FROM jobs WHERE lower(title) LIKE ? ORDER BY title LIMIT 6", (_like(p.get("title")),)).fetchall()
        con.close()
        j, err = _pick(rows, f"a position called {_clean(p.get('title'))}", lambda r: r["title"], "Say the full title and try again.")
        if err: return err
        return ask("delete_position", {"id": j["id"], "title": j["title"]}, f"Delete the position {j['title']} and its applications. This cannot be undone.")
    j = con.execute("SELECT * FROM jobs WHERE id=?", (p["id"],)).fetchone()
    if not j: con.close(); return fail("That position no longer exists.")
    con.execute("DELETE FROM jobs WHERE id=?", (j["id"],)); con.commit(); con.close()
    try:
        if j["jd_file"]: (config.UPLOAD_DIR / j["jd_file"]).unlink(missing_ok=True)
    except Exception: pass
    audit("DELETE", "JOB", j["id"], f"{j['title']} {VIA}")
    return say(f"Position {j['title']} deleted.")


@intent("complete_task")
def h_complete_task(u, p, confirmed):
    con = db()
    rows = con.execute("SELECT id,title FROM todos WHERE status<>'Done' AND lower(title) LIKE ? ORDER BY id LIMIT 6", (_like(p.get("title")),)).fetchall()
    t, err = _pick(rows, f"an open task matching {_clean(p.get('title'))}", lambda r: r["title"], "Say more of the task name.")
    if err: con.close(); return err
    con.execute("UPDATE todos SET status='Done' WHERE id=?", (t["id"],)); con.commit(); con.close()
    audit("UPDATE", "TODO", t["id"], f"marked done {VIA}")
    return say(f"Task {t['title']} marked as done.")


@intent("delete_task")
def h_delete_task(u, p, confirmed):
    con = db()
    if not confirmed:
        rows = con.execute("SELECT id,title FROM todos WHERE lower(title) LIKE ? ORDER BY id LIMIT 6", (_like(p.get("title")),)).fetchall(); con.close()
        t, err = _pick(rows, f"a task matching {_clean(p.get('title'))}", lambda r: r["title"], "Say more of the task name.")
        if err: return err
        return ask("delete_task", {"id": t["id"], "title": t["title"]}, f"Delete the task {t['title']}.")
    con.execute("DELETE FROM todos WHERE id=?", (p["id"],)); con.commit(); con.close()
    audit("DELETE", "TODO", p["id"], f"{p.get('title', '')} {VIA}")
    return say(f"Task {p.get('title', '')} deleted.")


# --------------------------------------------------------------------------- applications / interviews / AI
@intent("update_status")
def h_update_status(u, p, confirmed):
    if denied := need("manage_candidates"): return denied
    if confirmed:
        con = db()
        con.execute("UPDATE applications SET status=?,updated_at=? WHERE id=?",
                    (p["status"], datetime.now().isoformat(timespec="seconds"), p["aid"]))
        invite_created = False
        if p["status"] in {"Accepted", "Shortlisted", "Interview"}:
            from recruiter_ats.services.ai_interview import ensure_ai_interview_invite
            _, invite_created = ensure_ai_interview_invite(con, p["aid"])
        con.commit(); con.close()
        audit("STATUS_CHANGE", "APPLICATION", p["aid"], f"{p['status']} {VIA}")
        suffix = " A 48-hour AI first-round invitation is now available in the candidate's account." if invite_created else ""
        return say(f"{p['name']} is now {p['status']} for {p['job']}.{suffix}")
    status = next((s for s in STATUSES if s.lower() == _clean(p.get("status")).lower()), None)
    if not status: return fail("I can set the status to " + ", ".join(STATUSES[:-1]) + " or Rejected.")
    con = db()
    c, err = _candidate(con, p.get("candidate"))
    a, err = (None, err) if err else _application(con, c, p.get("job"))
    con.close()
    if err: return err
    params = {"aid": a["id"], "status": status, "name": c["name"], "job": a["title"]}
    if status in CONFIRM_STATUSES: return ask("update_status", params, f"Mark {c['name']} as {status} for {a['title']}.")
    return h_update_status(u, params, True)


@intent("schedule_interview")
def h_schedule_interview(u, p, confirmed):
    if denied := need("manage_interviews"): return denied
    if confirmed:
        from recruiter_ats.services.scheduling import clean_provider
        con = db()
        if not con.execute("SELECT 1 FROM applications WHERE id=?", (p["aid"],)).fetchone(): con.close(); return fail("That application no longer exists.")
        cur = con.execute("INSERT INTO interviews(application_id,round_name,scheduled_at,mode,interviewer,interviewer_user_id,meeting_link,meeting_provider,status) VALUES(?,?,?,?,?,?,?,?,?)",
                          (p["aid"], p["round"], p["when"], "Online", "", None, "", clean_provider(None), "Scheduled"))
        iid = cur.lastrowid; con.commit(); con.close()
        audit("CREATE", "INTERVIEW", iid, f"{p['round']} for {p['name']} at {p['when']} {VIA}")
        return say(f"Interview scheduled for {p['name']} on {_when_text(p['when'])}. Add an interviewer and meeting link on the Interviews page.")
    try: when = datetime.strptime(_clean(p.get("when")), "%Y-%m-%dT%H:%M")
    except ValueError: return fail("When? For example: schedule interview for Priya tomorrow at 11 am.")
    if when < datetime.now(): return fail("That time has already passed.")
    con = db()
    c, err = _candidate(con, p.get("candidate"))
    a, err = (None, err) if err else _application(con, c, p.get("job"))
    con.close()
    if err: return err
    round_name = _clean(p.get("round")) or "Round 1"
    return ask("schedule_interview", {"aid": a["id"], "when": when.strftime("%Y-%m-%dT%H:%M"), "round": round_name, "name": c["name"]},
               f"Schedule {round_name} for {c['name']}, {a['title']}, on {_when_text(when.strftime('%Y-%m-%dT%H:%M'))}.")


@intent("cancel_interview")
def h_cancel_interview(u, p, confirmed):
    if denied := need("manage_interviews"): return denied
    con = db()
    if confirmed:
        con.execute("UPDATE interviews SET status='Cancelled' WHERE id=?", (p["iid"],)); con.commit(); con.close()
        audit("UPDATE", "INTERVIEW", p["iid"], f"cancelled {VIA}")
        return say(f"Interview for {p['name']} cancelled.")
    c, err = _candidate(con, p.get("candidate"))
    if err: con.close(); return err
    rows = con.execute("""SELECT i.id,i.scheduled_at,i.round_name,j.title FROM interviews i JOIN applications a ON a.id=i.application_id
                          JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? AND i.status='Scheduled' ORDER BY i.scheduled_at""", (c["id"],)).fetchall(); con.close()
    if p.get("job"): rows = [r for r in rows if _clean(p["job"]).lower() in r["title"].lower()]
    if not rows: return fail(f"{c['name']} has no scheduled interview.")
    if len(rows) > 1: return fail(f"{c['name']} has {len(rows)} scheduled interviews: " + ", ".join(f"{r['title']} on {_when_text(r['scheduled_at'])}" for r in rows[:4]) + ". Add the position name, for example: cancel interview for " + c["name"] + f" for {rows[0]['title']}.", ambiguous=True)
    r = rows[0]
    return ask("cancel_interview", {"iid": r["id"], "name": c["name"]}, f"Cancel {r['round_name']} for {c['name']}, {r['title']}, on {_when_text(r['scheduled_at'])}.")


@intent("screen")
def h_screen(u, p, confirmed):
    if denied := need("manage_candidates"): return denied
    if confirmed:
        from recruiter_ats.routes.ai import ai_screen
        rv = ai_screen(p["aid"]); resp, code = (rv if isinstance(rv, tuple) else (rv, 200))
        data = resp.get_json(silent=True) or {}
        if not data.get("ok"): return fail(data.get("error") or "AI screening is not available right now.")
        r = data["data"]
        audit("AI_SCREENING", "APPLICATION", p["aid"], f"score={r['score']} {VIA}")
        return say(f"Screening finished. {p['name']} scored {r['score']} percent for {p['job']}. Status is now {r.get('status') or 'unchanged'}.")
    con = db()
    c, err = _candidate(con, p.get("candidate"))
    a, err = (None, err) if err else _application(con, c, p.get("job"))
    con.close()
    if err: return err
    return ask("screen", {"aid": a["id"], "name": c["name"], "job": a["title"]},
               f"Run AI screening for {c['name']} on {a['title']}. This can change the status and auto-schedule an interview, as the AI button does.")


# --------------------------------------------------------------------------- users
@intent("add_user")
def h_add_user(u, p, confirmed):
    if denied := need("manage_users"): return denied
    name, email = _clean(p.get("name")), _clean(p.get("email")).lower()
    role = next((r for r in ASSIGNABLE_ROLES if r.lower() == _clean(p.get("role") or "Recruiter").lower()), None)
    if not name or not EMAIL_RE.match(email): return fail("I need a name and a valid email, for example: add user Rahul Sharma, rahul at example dot com, as recruiter.")
    if not role: return fail("The role can be Recruiter, Manager, Interviewer or Admin.")
    if role == "Admin" and u["role"] != "Admin": return fail("Only an Admin can create another Admin.")
    con = db()
    if con.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone(): con.close(); return fail("A user with that email already exists.")
    if not confirmed:
        con.close()
        return ask("add_user", {"name": name, "email": email, "role": role}, f"Create the user {name}, email {email.replace('@', ' at ').replace('.', ' dot ')}, as {role}.")
    temp = secrets.token_urlsafe(12)
    cur = con.execute("INSERT INTO users(name,email,password_hash,role,created_at,totp_required,must_change_password) VALUES(?,?,?,?,?,?,1)",
                      (name, email, generate_password_hash(temp), role, datetime.now().isoformat(timespec="seconds"), 0 if role == "Admin" else 1))
    uid = cur.lastrowid; con.commit(); con.close()
    audit("CREATE", "USER", uid, f"{email} ({role}) {VIA}")
    return say(f"User {name} created as {role}. Their temporary password is on your screen only, and they must change it at first login.",
               screen_note=f"Temporary password for {email}: {temp}")


@intent("delete_user")
def h_delete_user(u, p, confirmed):
    if denied := need("manage_users"): return denied
    con = db()
    if confirmed:
        t = con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0", (p["id"],)).fetchone()
        if not t: con.close(); return fail("That user no longer exists.")
    else:
        q = _clean(p.get("name"))
        rows = con.execute("SELECT * FROM users WHERE COALESCE(is_super,0)=0 AND (lower(name)=lower(?) OR lower(email)=lower(?)) ORDER BY id", (q, q)).fetchall() \
            or con.execute("SELECT * FROM users WHERE COALESCE(is_super,0)=0 AND (lower(name) LIKE ? OR lower(email) LIKE ?) ORDER BY name LIMIT 6", (_like(q), _like(q))).fetchall()
        t, err = _pick(rows, f"a user called {q}", lambda r: r["name"], "Say the full name or the email and try again.")
        if err: con.close(); return err
    # same safeguards as the Admin page
    if t["id"] == u["id"]: con.close(); return fail("You cannot delete your own account.")
    if u["role"] != "Admin" and t["role"] == "Admin": con.close(); return fail("Managers cannot delete Admin accounts.")
    if t["role"] == "Admin" and con.execute("SELECT COUNT(*) c FROM users WHERE role='Admin' AND active=1 AND COALESCE(is_super,0)=0").fetchone()["c"] <= 1:
        con.close(); return fail("The last active Admin cannot be deleted.")
    if not confirmed:
        con.close()
        return ask("delete_user", {"id": t["id"]}, f"Delete the user {t['name']}, {t['role']}, {t['email']}. This cannot be undone.")
    uid = t["id"]
    con.execute("UPDATE jobs SET created_by=NULL WHERE created_by=?", (uid,)); con.execute("UPDATE todos SET assigned_to=NULL WHERE assigned_to=?", (uid,))
    con.execute("UPDATE audit_log SET user_id=NULL WHERE user_id=?", (uid,)); con.execute("DELETE FROM user_permissions WHERE user_id=?", (uid,))
    con.execute("DELETE FROM reminders WHERE user_id=?", (uid,)); con.execute("DELETE FROM users WHERE id=?", (uid,)); con.commit(); con.close()
    audit("USER_DELETED", "USER", None, f"{t['email']} {VIA}")
    return say(f"User {t['name']} deleted.")


# --------------------------------------------------------------------------- free-form language -> steps
# The fixed phrases in the browser stay as the fast, free path. Anything else is sent here: the AI only *translates*
# the sentence into the same intents the fixed phrases use. It never touches data itself, so every permission check,
# yes/no confirmation and audit entry above still applies to whatever it proposes.
CLIENT_INTENTS = {"reminder", "task", "briefing"}
MAX_STEPS = 4
INTENT_CATALOG = """open_page {page: dashboard|candidates|positions|jobs|applications|ai screening|calendar|interviews|team|meetings|reports|admin|settings}
search {q: free text name/email/skill/position to look up}
count {what: candidates|applications|positions|interviews}
add_candidate {name, email?, phone?}
delete_candidate {name}
add_position {title}
delete_position {title}
complete_task {title}
delete_task {title}
update_status {candidate, status: Applied|Screening|Accepted|Hold|Shortlisted|Interview|Offer|Hired|Rejected, job?}
schedule_interview {candidate, job?, when: local "YYYY-MM-DDTHH:MM", round?: "Round 1"}
cancel_interview {candidate, job?}
screen {candidate, job?}
add_user {name, email, role: recruiter|manager|interviewer|admin}
delete_user {name}
reminder {text, at: local "YYYY-MM-DDTHH:MM"}
task {title, due_date?: "YYYY-MM-DD"}
briefing {part: all|daily|custom|reminders}"""


def _interpret_prompt(text):
    now = datetime.now()
    return f"""You turn one spoken or typed sentence from a recruiter into actions for an applicant-tracking app.
Now: {now.strftime('%A %Y-%m-%d %H:%M')} (local time). Resolve relative dates/times ("tomorrow 3pm", "next Friday") to absolute local values.
Allowed intents and params:
{INTENT_CATALOG}
Rules:
- Use only the intents above, with params exactly as named. Never invent intents.
- Up to {MAX_STEPS} steps, in the order they should run. One sentence may contain several requests.
- Copy names/titles exactly as the user said them; do not guess missing details. If something required is missing, return no steps and put a short question in "reply".
- Questions about the recruiter's day, tasks or reminders -> briefing. Looking someone up or "tell me about X" -> search.
- If the sentence is just chatter or not something the app can do, return no steps and a one-line "reply" explaining.
Return ONLY JSON: {{"steps":[{{"intent":"","params":{{}}}}],"reply":""}}
SENTENCE: {text}"""


@app.route("/api/voice-service/interpret", methods=["POST"])
@staff_only
def vs_interpret():
    from recruiter_ats.services.ai import ai_json, ai_task_on
    d = request.get_json(silent=True) or {}
    text = _clean(d.get("text"))[:500]
    if not text: return jsonify(ok=False, error="Nothing to interpret."), 400
    if not ai_task_on("ai_master"):
        return jsonify(ok=False, unavailable=True, error="The AI assistant is switched off.")
    data, err = ai_json(_interpret_prompt(text))
    if err or not isinstance(data, dict):
        return jsonify(ok=False, unavailable=True, error=err or "AI gave no answer.")
    steps = []
    for st in (data.get("steps") or [])[:MAX_STEPS]:
        if not isinstance(st, dict): continue
        name = st.get("intent")
        if name not in HANDLERS and name not in CLIENT_INTENTS: continue
        params = st.get("params") if isinstance(st.get("params"), dict) else {}
        steps.append({"intent": name, "params": {str(k)[:30]: _clean(v)[:300] for k, v in params.items() if isinstance(v, (str, int, float))}})
    return jsonify(ok=True, steps=steps, reply=_clean(data.get("reply"))[:300])
