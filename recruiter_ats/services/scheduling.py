"""Working-hours settings and automatic interview slot booking."""
import os
import uuid
from datetime import datetime, timedelta
from flask import request
from recruiter_ats.config import SCHEDULE_LOCK
from recruiter_ats.database import db, get_setting
from recruiter_ats.security import audit
from recruiter_ats.services.mailer import get_smtp_config, queue_email


MEETING_PROVIDERS = ["Microsoft Teams", "Google Meet", "Zoom", "Custom Meeting"]
DEFAULT_PROVIDER = "Microsoft Teams"


def clean_provider(value):
    """Return a supported provider name (the old built-in ATS meeting maps to the default)."""
    value = (value or "").strip()
    for p in MEETING_PROVIDERS:
        if value.lower() == p.lower():
            return p
    return DEFAULT_PROVIDER


def clean_meeting_link(value):
    """Return a safe http(s) meeting URL, "" when empty, or None when invalid (blocks javascript: etc.)."""
    value = (value or "").strip()
    if not value:
        return ""
    if " " in value or len(value) > 500:
        return None
    from urllib.parse import urlparse
    u = urlparse(value)
    return value if u.scheme in ("http", "https") and u.netloc else None


def get_base_url():
    """Public base URL used in meeting links inside emails."""
    base = os.getenv("ARS_BASE_URL", "").strip() or get_setting("public_base_url", "").strip()
    if not base:
        try:
            base = request.host_url
        except RuntimeError:
            base = "http://localhost:5001/"
    return base.rstrip("/")

# ---- interview scheduling settings -----------------------------------------
def _hhmm_to_min(value, default):
    try:
        h, m = str(value).strip().split(":")[:2]
        return max(0, min(1439, int(h) * 60 + int(m)))
    except Exception:
        return default

def _bounded_int(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(float(get_setting(key, str(default))))))
    except (TypeError, ValueError):
        return default

def sched_config():
    days = [int(x) for x in get_setting("sched_work_days", "0,1,2,3,4").split(",")
            if x.strip().isdigit() and 0 <= int(x.strip()) <= 6]
    cfg = {
        "enabled": get_setting("sched_enabled", "1") == "1",
        "days": days or [0, 1, 2, 3, 4],
        "start": _hhmm_to_min(get_setting("sched_start", "10:00"), 600),
        "end": _hhmm_to_min(get_setting("sched_end", "18:00"), 1080),
        "duration": _bounded_int("sched_duration", 45, 15, 240),
        "buffer": _bounded_int("sched_buffer", 15, 0, 120),
        "lead_hours": _bounded_int("sched_lead_hours", 4, 0, 168),
        "horizon": _bounded_int("sched_horizon_days", 14, 1, 60),
        "provider": clean_provider(get_setting("sched_provider", DEFAULT_PROVIDER)),
        "meeting_link": clean_meeting_link(get_setting("sched_meeting_link", "")) or "",
        "round": get_setting("sched_round", "Round 1") or "Round 1",
        "notify_interviewer": get_setting("sched_notify_interviewer", "1") == "1",
    }
    if cfg["end"] - cfg["start"] < cfg["duration"]:
        cfg["start"], cfg["end"] = 600, 1080
    return cfg

def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).strip())
    except Exception:
        return None

def find_free_slot(con, candidate_id, interviewers, cfg, now=None):
    """Check the interview calendar and return (start_datetime, interviewer) for the earliest free slot.
    A slot is free when neither the interviewer nor the candidate has an overlapping interview
    (including the configured buffer) and it falls inside working days/hours."""
    now = now or datetime.now()
    earliest = now + timedelta(hours=cfg["lead_hours"])
    dur = timedelta(minutes=cfg["duration"]); buf = timedelta(minutes=cfg["buffer"])
    busy = []
    rows = con.execute("""SELECT i.scheduled_at,i.interviewer_user_id,i.interviewer,a.candidate_id
                          FROM interviews i JOIN applications a ON a.id=i.application_id
                          WHERE COALESCE(i.status,'Scheduled') NOT IN ('Cancelled','Completed')
                            AND i.scheduled_at IS NOT NULL AND i.scheduled_at<>''""").fetchall()
    for r in rows:
        s = _parse_dt(r["scheduled_at"])
        if s and s >= now - timedelta(days=1):
            key = r["interviewer_user_id"] or (r["interviewer"] or "").strip().lower()
            busy.append((s, s + dur, key, r["candidate_id"]))
    def clash(start, end, key=None, cand=None):
        for bs, be, bkey, bcand in busy:
            if (key is not None and bkey == key) or (cand is not None and bcand == cand):
                if start < be + buf and end > bs - buf:
                    return True
        return False
    load = {}
    for bs, be, bkey, bcand in busy:
        if bs >= now:
            load[bkey] = load.get(bkey, 0) + 1
    def ikey(iv): return iv["id"] or (iv["name"] or "").strip().lower()
    ordered = sorted(interviewers, key=lambda iv: load.get(ikey(iv), 0))
    day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(cfg["horizon"] + 1):
        if day.weekday() in cfg["days"]:
            minute = cfg["start"]
            while minute + cfg["duration"] <= cfg["end"]:
                start = day + timedelta(minutes=minute)
                minute += 30
                if start < earliest:
                    continue
                end = start + dur
                if clash(start, end, cand=candidate_id):
                    continue
                for iv in ordered:
                    if not clash(start, end, key=ikey(iv)):
                        return start, iv
        day += timedelta(days=1)
    return None, None

def auto_schedule_interview(aid, actor_id=None, base_url=""):
    """Find a free slot on the calendar, create the interview + meeting link and queue the emails.
    Runs only when the application has no active interview yet, so re-screening never double-books."""
    cfg = sched_config()
    if not cfg["enabled"]:
        return {"status": "disabled", "message": "Automatic interview scheduling is turned off in Settings."}
    base_url = (base_url or get_base_url()).rstrip("/")
    with SCHEDULE_LOCK:
        con = db()
        try:
            row = con.execute("""SELECT a.id,a.candidate_id,c.name candidate,c.email candidate_email,
                                        j.title job,co.name company
                                 FROM applications a JOIN candidates c ON c.id=a.candidate_id
                                 JOIN jobs j ON j.id=a.job_id LEFT JOIN companies co ON co.id=j.company_id
                                 WHERE a.id=?""", (aid,)).fetchone()
            if not row:
                return {"status": "error", "message": "Application not found."}
            existing = con.execute("""SELECT scheduled_at,interviewer,meeting_link FROM interviews
                                      WHERE application_id=? AND COALESCE(status,'Scheduled')<>'Cancelled'
                                      ORDER BY id DESC LIMIT 1""", (aid,)).fetchone()
            if existing:
                return {"status": "already_scheduled", "scheduled_at": existing["scheduled_at"],
                        "interviewer": existing["interviewer"], "meeting_link": existing["meeting_link"] or "",
                        "message": "Interview already scheduled for this position."}
            interviewers = [{"id": r["id"], "name": r["name"], "email": r["email"]} for r in
                            con.execute("SELECT id,name,email FROM users WHERE role='Interviewer' AND active=1 ORDER BY name").fetchall()]
            if not interviewers:
                actor = con.execute("SELECT id,name,email FROM users WHERE id=? AND active=1", (actor_id,)).fetchone() if actor_id else None
                interviewers = [{"id": actor["id"], "name": actor["name"], "email": actor["email"]} if actor
                                else {"id": None, "name": "Recruiter", "email": ""}]
            start, iv = find_free_slot(con, row["candidate_id"], interviewers, cfg)
            if not start:
                return {"status": "no_slot", "message": f"No free slot in the next {cfg['horizon']} days. Schedule manually."}
            provider = cfg["provider"]; link = cfg["meeting_link"]
            when_iso = start.strftime("%Y-%m-%dT%H:%M")
            con.execute("""INSERT INTO interviews(application_id,round_name,scheduled_at,mode,interviewer,interviewer_user_id,meeting_link,meeting_provider,status)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (aid, cfg["round"], when_iso, "Online", iv["name"], iv["id"], link, provider, "Scheduled"))
            iid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.commit()
        finally:
            con.close()

    # Emails are queued after the slot is committed (outside the lock).
    cfgm = get_smtp_config()
    smtp_ready = bool(cfgm["host"] and cfgm["from_email"])
    sender = cfgm["from_name"] or "Recruiter ARS"
    when_text = start.strftime("%A, %d %B %Y at %I:%M %p")
    company = row["company"] or "our company"
    link_line = link if link else f"{provider} - the recruiter will share the joining link."
    email_status = "no_email"
    con = db()
    try:
        cand_email = (row["candidate_email"] or "").strip()
        if cand_email:
            body = (f"Dear {row['candidate']},\n\n"
                    f"Thank you for applying for the {row['job']} position at {company}. "
                    f"After reviewing your resume, we would like to invite you for an interview.\n\n"
                    f"Interview details\n"
                    f"-----------------\n"
                    f"Position   : {row['job']}\n"
                    f"Round      : {cfg['round']}\n"
                    f"Date & time: {when_text}\n"
                    f"Duration   : {cfg['duration']} minutes\n"
                    f"Mode       : Online\n"
                    f"Meeting    : {link_line}\n\n"
                    f"Please reply to this email if you need to reschedule.\n\n"
                    f"Best regards,\n{sender}")
            queue_email(con, aid, "Candidate", row["candidate"], cand_email,
                        f"Interview scheduled - {row['job']} ({company})", body, actor_id)
            email_status = "queued" if smtp_ready else "queued_smtp_missing"
        iv_email = (iv.get("email") or "").strip()
        if cfg["notify_interviewer"] and iv_email:
            body = (f"Hello {iv['name']},\n\n"
                    f"An interview has been scheduled for you.\n\n"
                    f"Candidate : {row['candidate']}\n"
                    f"Position  : {row['job']}\n"
                    f"Date & time: {when_text}\n"
                    f"Duration  : {cfg['duration']} minutes\n"
                    f"Meeting   : {link_line}\n\n"
                    f"Regards,\n{sender}")
            queue_email(con, aid, "Interviewer", iv["name"], iv_email,
                        f"Interview assigned - {row['candidate']} for {row['job']}", body, actor_id)
        con.commit()
    finally:
        con.close()
    audit("AUTO_SCHEDULE", "INTERVIEW", iid, f"{row['candidate']} / {row['job']} @ {when_iso} with {iv['name']}", actor_id=actor_id)
    return {"status": "scheduled", "interview_id": iid, "scheduled_at": when_iso, "when_text": when_text,
            "interviewer": iv["name"], "meeting_link": link, "email": email_status}
