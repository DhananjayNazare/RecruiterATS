"""Shared helpers for the candidate-led AI first-round interview workflow."""
import json
from datetime import datetime, timedelta

AI_ROUND_NAME = "AI Interview - Round 1"
AI_INTERVIEWER_NAME = "AI Interviewer"


def ensure_ai_interview_invite(con, application_id):
    """Create or reopen one 48-hour candidate scheduling window per application.

    Repeated recruiter status changes do not create duplicate interviews. An expired,
    incomplete AI invitation is reopened with a fresh window; completed interviews are
    preserved as evidence and are never reset by this helper.
    """
    existing = con.execute(
        """SELECT id,status,available_until FROM interviews WHERE application_id=? AND round_name=?
           AND COALESCE(status,'')<>'Cancelled' ORDER BY id DESC LIMIT 1""",
        (application_id, AI_ROUND_NAME),
    ).fetchone()
    start = datetime.now()
    end = start + timedelta(hours=48)
    start_text = start.isoformat(timespec="seconds")
    end_text = end.isoformat(timespec="seconds")
    if existing:
        status = str(existing["status"] or "")
        try:
            window_end = datetime.fromisoformat(existing["available_until"]) if existing["available_until"] else None
        except (TypeError, ValueError):
            window_end = None
        expired = window_end is None or window_end < start
        if expired and status not in {"Completed", "In Progress"}:
            con.execute(
                """UPDATE interviews SET scheduled_at=NULL,mode='AI',interviewer=?,meeting_provider='AI Interview',
                   status='Awaiting Candidate',available_from=?,available_until=?,ai_questions=NULL,ai_answers=NULL,
                   ai_report=NULL,ai_tracking=NULL,ai_started_at=NULL,ai_completed_at=NULL,ai_feedback=NULL
                   WHERE id=?""",
                (AI_INTERVIEWER_NAME, start_text, end_text, existing["id"]),
            )
            return existing["id"], True
        return existing["id"], False
    cur = con.execute(
        """INSERT INTO interviews(
               application_id,round_name,scheduled_at,mode,interviewer,meeting_provider,
               status,available_from,available_until
           ) VALUES(?,?,NULL,'AI',?,'AI Interview','Awaiting Candidate',?,?)""",
        (application_id, AI_ROUND_NAME, AI_INTERVIEWER_NAME, start_text, end_text),
    )
    return cur.lastrowid, True


def decode_json(value, fallback=None):
    if fallback is None:
        fallback = {}
    try:
        parsed = json.loads(value) if value else fallback
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError):
        return fallback
