"""Offline voice assistant API: local briefing, tasks and the user's own reminders, no AI model involved."""
from datetime import datetime, timedelta
from flask import jsonify, request, session
from recruiter_ats.config import app
from recruiter_ats.database import db
from recruiter_ats.security import audit, current_user, staff_only
from recruiter_ats.services import assistant as svc


@app.route("/api/voice-assistant/briefing")
@staff_only
def voice_briefing():
    part = request.args.get("part", "all")
    if part not in {"all", "daily", "custom", "reminders"}:
        part = "all"
    u = current_user()
    data = svc.collect(user_id=u["id"] if u else None)
    return jsonify(ok=True, speech=svc.speech(data, (u["name"] if u else ""), part),
                   data=data, reminders=svc.reminders(data))


@app.route("/api/voice-assistant/task", methods=["POST"])
@staff_only
def voice_add_task():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()[:200]
    due = (payload.get("due_date") or "").strip()[:10]
    if not title:
        return jsonify(ok=False, error="Task name is required."), 400
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            due = ""
    con = db()
    cur = con.execute("INSERT INTO todos(title,due_date,assigned_to,created_at) VALUES(?,?,?,?)",
                      (title, due, session["user_id"], datetime.now().isoformat(timespec="seconds")))
    con.commit(); tid = cur.lastrowid; con.close()
    audit("CREATE", "TODO", tid, "via voice assistant")
    return jsonify(ok=True, id=tid, title=title, due_date=due)


# ---- user-set reminders (stored per user, so they survive a browser clear and follow the user between PCs) ----
def _parse_when(value):
    value = (value or "").strip().replace(" ", "T")[:16]
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _reminder_json(r):
    return {"id": r["id"], "text": r["text"], "at": r["remind_at"]}


@app.route("/api/voice-assistant/reminders")
@staff_only
def voice_reminders():
    con = db()
    rows = con.execute("SELECT id,text,remind_at FROM reminders WHERE user_id=? AND status='Pending' "
                       "ORDER BY remind_at,id", (session["user_id"],)).fetchall()
    con.close()
    return jsonify(ok=True, reminders=[_reminder_json(r) for r in rows])


@app.route("/api/voice-assistant/reminders", methods=["POST"])
@staff_only
def voice_add_reminder():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()[:200]
    when = _parse_when(payload.get("at"))
    if not text:
        return jsonify(ok=False, error="Tell me what to remind you about."), 400
    if not when:
        return jsonify(ok=False, error="Pick a valid date and time."), 400
    if when < datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=1):
        return jsonify(ok=False, error="That time has already passed. Pick a future time."), 400
    con = db()
    cur = con.execute("INSERT INTO reminders(user_id,text,remind_at,created_at) VALUES(?,?,?,?)",
                      (session["user_id"], text, when.strftime("%Y-%m-%dT%H:%M"),
                       datetime.now().isoformat(timespec="seconds")))
    con.commit(); rid = cur.lastrowid; con.close()
    return jsonify(ok=True, reminder={"id": rid, "text": text, "at": when.strftime("%Y-%m-%dT%H:%M")})


@app.route("/api/voice-assistant/reminders/<int:rid>/done", methods=["POST"])
@staff_only
def voice_reminder_done(rid):
    con = db()
    con.execute("UPDATE reminders SET status='Done' WHERE id=? AND user_id=?", (rid, session["user_id"]))
    con.commit(); con.close()
    return jsonify(ok=True)


@app.route("/api/voice-assistant/reminders/<int:rid>/delete", methods=["POST"])
@staff_only
def voice_reminder_delete(rid):
    con = db()
    con.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (rid, session["user_id"]))
    con.commit(); con.close()
    return jsonify(ok=True)
