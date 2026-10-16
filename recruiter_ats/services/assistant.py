"""Voice assistant data: daily-task + custom-task briefing and the user's own reminders.

100% rule-based and local (SQLite only). It never calls an AI model or any
external service, so the briefing works when the AI model / internet is offline.
The browser speaks the returned text with the device's built-in speech engine.
"""
from datetime import datetime, timedelta
from recruiter_ats.database import db


def _parse(ts):
    try:
        return datetime.fromisoformat((ts or "").replace(" ", "T")[:16])
    except ValueError:
        return None


def _clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def collect(now=None, user_id=None):
    """Return structured daily tasks, custom tasks and the user's own reminders."""
    now = now or datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    con = db()
    try:
        rows = con.execute(
            """SELECT i.id,i.round_name,i.scheduled_at,i.mode,c.name candidate,j.title job
               FROM interviews i JOIN applications a ON a.id=i.application_id
               JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
               WHERE COALESCE(i.status,'Scheduled') NOT IN ('Cancelled','Completed')
                 AND i.scheduled_at IS NOT NULL AND i.scheduled_at<>''
               ORDER BY i.scheduled_at""").fetchall()
        interviews_today, interviews_tomorrow = [], []
        for r in rows:
            dt = _parse(r["scheduled_at"])
            if not dt:
                continue
            item = {"id": r["id"], "candidate": r["candidate"], "job": r["job"],
                    "round": r["round_name"] or "Interview", "mode": r["mode"] or "",
                    "at": dt.isoformat(timespec="minutes"), "time": _clock(dt)}
            if dt.date() == today:
                interviews_today.append(item)
            elif dt.date() == tomorrow:
                interviews_tomorrow.append(item)

        emails = []
        for r in con.execute(
                "SELECT id,recipient_name,recipient_email,subject,scheduled_at FROM email_schedules "
                "WHERE status='Pending' ORDER BY scheduled_at").fetchall():
            dt = _parse(r["scheduled_at"])
            if dt and dt.date() <= today:
                emails.append({"id": r["id"], "to": r["recipient_name"] or r["recipient_email"],
                               "subject": r["subject"], "at": dt.isoformat(timespec="minutes"),
                               "time": _clock(dt), "overdue": dt < now})

        new_apps = con.execute(
            "SELECT COUNT(*) c FROM applications WHERE status='Applied'").fetchone()["c"]
        apps_today = con.execute(
            "SELECT COUNT(*) c FROM applications WHERE substr(created_at,1,10)=?",
            (today.isoformat(),)).fetchone()["c"]

        overdue, due_today, upcoming, undated = [], [], [], []
        for t in con.execute("SELECT id,title,due_date FROM todos WHERE status='Open' ORDER BY due_date,id").fetchall():
            item = {"id": t["id"], "title": t["title"], "due": t["due_date"] or ""}
            d = (t["due_date"] or "")[:10]
            if not d:
                undated.append(item)
            elif d < today.isoformat():
                overdue.append(item)
            elif d == today.isoformat():
                due_today.append(item)
            else:
                upcoming.append(item)
        my_reminders = []
        if user_id:
            for r in con.execute("SELECT id,text,remind_at FROM reminders WHERE user_id=? AND status='Pending' "
                                 "ORDER BY remind_at,id", (user_id,)).fetchall():
                dt = _parse(r["remind_at"])
                if dt:
                    my_reminders.append({"id": r["id"], "text": r["text"], "at": dt.isoformat(timespec="minutes"),
                                         "time": _clock(dt), "date": dt.strftime("%a %d %b"),
                                         "today": dt.date() == today})
    finally:
        con.close()

    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "daily": {"interviews_today": interviews_today, "interviews_tomorrow": interviews_tomorrow,
                  "emails_due": emails, "applications_awaiting_review": new_apps,
                  "applications_today": apps_today},
        "custom": {"overdue": overdue, "due_today": due_today, "upcoming": upcoming, "undated": undated},
        "my_reminders": my_reminders,
    }


def _titles(items, limit=5):
    names = [i["title"] for i in items[:limit]]
    extra = len(items) - len(names)
    text = "; ".join(names)
    return text + (f"; and {extra} more" if extra > 0 else "")


def speech(data, name="", part="all"):
    """Plain-English script for text-to-speech. part: all|daily|custom|reminders."""
    d, c, now = data["daily"], data["custom"], _parse(data["generated_at"]) or datetime.now()
    hour = now.hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    out = []
    if part == "all":
        out.append(f"{greet}{', ' + name.split()[0] if name else ''}. Here is your summary for {now.strftime('%A, %d %B')}.")

    if part in ("all", "daily"):
        iv = d["interviews_today"]
        if iv:
            out.append(f"Daily tasks. You have {_plural(len(iv), 'interview')} today.")
            for i in iv[:8]:
                out.append(f"At {i['time']}, {i['round']} with {i['candidate']} for {i['job']}.")
        else:
            out.append("Daily tasks. You have no interviews today.")
        if d["interviews_tomorrow"]:
            out.append(f"Tomorrow you have {_plural(len(d['interviews_tomorrow']), 'interview')}, "
                       f"the first at {d['interviews_tomorrow'][0]['time']}.")
        if d["emails_due"]:
            od = sum(1 for e in d["emails_due"] if e["overdue"])
            out.append(f"{_plural(len(d['emails_due']), 'scheduled email')} {'is' if len(d['emails_due'])==1 else 'are'} "
                       f"due today{f', {od} already past time' if od else ''}.")
        if d["applications_awaiting_review"]:
            out.append(f"{_plural(d['applications_awaiting_review'], 'application')} "
                       f"{'is' if d['applications_awaiting_review']==1 else 'are'} waiting for your review.")
        if d["applications_today"]:
            out.append(f"{_plural(d['applications_today'], 'new application')} came in today.")

    if part in ("all", "custom"):
        total = sum(len(c[k]) for k in c)
        if not total:
            out.append("Custom tasks. Your to-do list is empty.")
        else:
            out.append(f"Custom tasks. You have {_plural(total, 'open task')}.")
            if c["overdue"]:
                out.append(f"{len(c['overdue'])} overdue: {_titles(c['overdue'])}.")
            if c["due_today"]:
                out.append(f"{len(c['due_today'])} due today: {_titles(c['due_today'])}.")
            if c["upcoming"]:
                out.append(f"{len(c['upcoming'])} upcoming: {_titles(c['upcoming'], 3)}.")
            if c["undated"]:
                out.append(f"{len(c['undated'])} with no due date: {_titles(c['undated'], 3)}.")

    mine = data.get("my_reminders", [])
    if part == "reminders" or (part == "all" and mine):      # the full summary only mentions reminders when you have some
        if not mine:
            out.append("Reminders. You have no reminders set.")
        else:
            out.append(f"Reminders. You have set {_plural(len(mine), 'reminder')}.")
            for r in mine[:8]:
                when = f"today at {r['time']}" if r["today"] else f"on {r['date']} at {r['time']}"
                out.append(f"{r['text']}, {when}.")
            if len(mine) > 8:
                out.append(f"And {len(mine) - 8} more.")

    if part == "all":
        urgent = len(d["interviews_today"]) + len(c["overdue"]) + len(c["due_today"])
        out.append("That is everything." if urgent else "Nothing urgent. Have a great day.")
    return " ".join(out)


def reminders(data):
    """Timed items the browser should remind about today. lead = minutes before."""
    items = []
    for i in data["daily"]["interviews_today"]:
        items.append({"key": f"iv{i['id']}", "at": i["at"], "lead": 15,
                      "text": f"Interview with {i['candidate']} for {i['job']} at {i['time']}."})
    for e in data["daily"]["emails_due"]:
        items.append({"key": f"em{e['id']}", "at": e["at"], "lead": 0,
                      "text": f"Scheduled email to {e['to']} is due: {e['subject']}."})
    for t in data["custom"]["due_today"] + data["custom"]["overdue"]:
        items.append({"key": f"td{t['id']}", "at": None, "lead": 0,
                      "text": f"Task due: {t['title']}."})
    return items
