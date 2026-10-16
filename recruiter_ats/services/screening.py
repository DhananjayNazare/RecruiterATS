"""Resume screening pipeline: parse, score, de-duplicate and batch runs."""
import json
import os
from datetime import datetime
from recruiter_ats import config
from recruiter_ats.config import BATCH_LOCK, app
from recruiter_ats.database import db
from recruiter_ats.security import audit
from recruiter_ats.services.ai import _to_float, ai_configured, ai_parse_and_screen, ai_task_on, screening_band
from recruiter_ats.services.scheduling import auto_schedule_interview


def apply_screening_result(aid, score, result, threshold, actor_id=None, base_url="", schedule=True):
    """Apply the AI score to an application: set the pipeline status and, when the score reaches the
    threshold, automatically schedule the interview and email the candidate."""
    decision, status = screening_band(score, threshold)
    suggested = status
    decide, book = ai_task_on("ai_decide_status"), ai_task_on("ai_auto_schedule")
    payload = {**result, "score": score, "decision": decision, "threshold": threshold,
               "suggested_status": suggested, "ai_decided": decide}
    now = datetime.now().isoformat(timespec="seconds")
    con = db()
    try:
        cur = con.execute("SELECT status FROM applications WHERE id=?", (aid,)).fetchone()
        keep = bool(cur and cur["status"] in ("Offer", "Hired"))   # never pull back a candidate who is already further along
        cur_status = cur["status"] if cur else "Applied"
        if keep:
            status = cur_status
        elif not decide:      # AI decisions are OFF: score is only a suggestion, the recruiter chooses the status
            status = cur_status if cur_status not in ("Applied", "") else "Screening"
        elif status == "Interview" and not book and cur_status != "Interview":
            status = "Shortlisted"   # AI booking is OFF: suggest the interview, recruiter schedules it
            payload["note"] = "AI suggests an interview - schedule it manually."
        con.execute("UPDATE applications SET match_score=?,ai_screening=?,status=?,ai_auto_processed_at=COALESCE(ai_auto_processed_at,?),updated_at=? WHERE id=?",
                    (score, json.dumps(payload, ensure_ascii=False), status, now, now, aid))
        con.commit()
    finally:
        con.close()
    out = {"decision": decision, "status": status, "score": score, "interview": None,
           "ai_decided": decide, "suggested_status": suggested}
    if status == "Interview" and schedule and book and decide and not keep:
        try:
            out["interview"] = auto_schedule_interview(aid, actor_id=actor_id, base_url=base_url)
        except Exception as exc:
            app.logger.exception("Auto scheduling failed for application %s", aid)
            out["interview"] = {"status": "error", "message": str(exc)}
    return out

def merge_duplicate_candidate(cid):
    """If another candidate already has the same email, keep the older record and move the new resume onto it."""
    con = db()
    try:
        c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        email = (c["email"] or "").strip().lower() if c else ""
        if not email:
            return cid, False
        other = con.execute("SELECT id,resume_file FROM candidates WHERE id<>? AND lower(trim(email))=? ORDER BY id LIMIT 1", (cid, email)).fetchone()
        if not other:
            return cid, False
        con.execute("""UPDATE candidates SET resume_file=?,resume_text=?,skills=?,experience_years=?,education=?,certifications=?,
                       ai_summary=?,ai_strengths=?,name=COALESCE(NULLIF(?,''),name),phone=COALESCE(NULLIF(?,''),phone),
                       location=COALESCE(NULLIF(?,''),location),updated_at=? WHERE id=?""",
                    (c["resume_file"], c["resume_text"], c["skills"], c["experience_years"], c["education"], c["certifications"],
                     c["ai_summary"], c["ai_strengths"], c["name"], c["phone"], c["location"],
                     datetime.now().isoformat(timespec="seconds"), other["id"]))
        con.execute("DELETE FROM candidates WHERE id=?", (cid,))
        con.commit()
        if other["resume_file"] and other["resume_file"] != c["resume_file"]:
            try: (config.UPLOAD_DIR / other["resume_file"]).unlink(missing_ok=True)
            except Exception: pass
        return other["id"], True
    finally:
        con.close()

def process_resume_for_position(cid, job_id, threshold, actor_id=None, base_url="", schedule=True):
    """Full workflow for one resume: parse -> attach to the position (JD) -> score -> decision ->
    auto-schedule interview -> email candidate. Used by batch processing and AI Screening."""
    con = db()
    c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    job = con.execute("""SELECT j.*,co.name company FROM jobs j LEFT JOIN companies co ON co.id=j.company_id WHERE j.id=?""", (job_id,)).fetchone()
    con.close()
    res = {"candidate_id": cid, "name": c["name"] if c else "", "position": job["title"] if job else ""}
    if not c or not job:
        return {**res, "error": "Candidate or position not found."}
    text = c["resume_text"] or ""
    if not text.strip() or text.startswith("[Resume extraction error"):
        return {**res, "error": "Resume text could not be read (scanned/image PDF?)."}
    now = datetime.now().isoformat(timespec="seconds")

    def attach(status="Applied", source="Resume Screening"):
        con = db()
        try:
            con.execute("""INSERT INTO applications(candidate_id,job_id,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?)
                           ON CONFLICT(candidate_id,job_id) DO UPDATE SET updated_at=excluded.updated_at""",
                        (cid, job_id, status, source, now, now))
            con.commit()
            return con.execute("SELECT id FROM applications WHERE candidate_id=? AND job_id=?", (cid, job_id)).fetchone()["id"]
        finally:
            con.close()

    if not ai_configured():
        aid = attach()
        return {**res, "application_id": aid, "status": "Applied", "note": "AI is not configured - attached to the position without screening."}
    data, err = ai_parse_and_screen(text, job)
    if err:
        aid = attach()
        return {**res, "application_id": aid, "status": "Applied", "error": f"AI screening failed: {err}"}

    def as_list(v): return v if isinstance(v, list) else []
    con = db()
    try:
        con.execute("""UPDATE candidates SET name=?,email=?,phone=?,location=?,skills=?,experience_years=?,education=?,certifications=?,
                       ai_summary=?,ai_strengths=?,updated_at=? WHERE id=?""",
                    (data.get("name") or c["name"], data.get("email") or c["email"], data.get("phone") or c["phone"],
                     data.get("location") or c["location"], ", ".join(map(str, as_list(data.get("skills")))),
                     _to_float(data.get("experience_years")),
                     ", ".join(map(str, as_list(data.get("education")))), ", ".join(map(str, as_list(data.get("certifications")))),
                     data.get("summary", ""), ", ".join(map(str, as_list(data.get("strengths")))), now, cid))
        con.commit()
    finally:
        con.close()
    cid, merged = merge_duplicate_candidate(cid)
    res["candidate_id"] = cid
    con = db(); res["name"] = con.execute("SELECT name FROM candidates WHERE id=?", (cid,)).fetchone()["name"]; con.close()
    aid = attach()
    try: score = max(0, min(100, int(float(data.get("score", 0)))))
    except (TypeError, ValueError): score = 0
    screening = {"matching_keywords": as_list(data.get("matching_keywords")), "missing_requirements": as_list(data.get("missing_requirements")),
                 "why_match": as_list(data.get("why_match"))}
    out = apply_screening_result(aid, score, screening, threshold, actor_id=actor_id, base_url=base_url, schedule=schedule)
    iv = out.get("interview") or {}
    res.update({"application_id": aid, "score": score, "decision": out["decision"], "status": out["status"],
                "interview_status": iv.get("status", ""), "interview_at": iv.get("scheduled_at", ""),
                "interviewer": iv.get("interviewer", ""), "meeting_link": iv.get("meeting_link", ""),
                "email_status": iv.get("email", ""), "note": iv.get("message", "") or ("Existing candidate record updated." if merged else "")})
    audit("AI_SCREEN_PIPELINE", "APPLICATION", aid, f"{res['name']} -> {job['title']} score={score} {out['decision']}", actor_id=actor_id)
    return res

def _batch_update(run_id, idx, **fields):
    with BATCH_LOCK:
        con = db()
        try:
            row = con.execute("SELECT results FROM batch_runs WHERE id=?", (run_id,)).fetchone()
            if not row: return
            items = json.loads(row["results"] or "[]")
            if idx < len(items): items[idx].update(fields)
            done = sum(1 for x in items if x.get("state") in ("Done", "Error"))
            con.execute("UPDATE batch_runs SET results=?,done=? WHERE id=?", (json.dumps(items, ensure_ascii=False), done, run_id))
            con.commit()
        finally:
            con.close()

def run_batch(run_id, job_id, threshold, actor_id, base_url):
    """Background worker: runs the full screening workflow for every resume in a batch."""
    from concurrent.futures import ThreadPoolExecutor
    con = db(); row = con.execute("SELECT results FROM batch_runs WHERE id=?", (run_id,)).fetchone(); con.close()
    count = len(json.loads(row["results"] or "[]")) if row else 0
    def work(idx):
        con = db(); items = json.loads(con.execute("SELECT results FROM batch_runs WHERE id=?", (run_id,)).fetchone()["results"]); con.close()
        cid = items[idx]["candidate_id"]
        _batch_update(run_id, idx, state="Processing")
        try:
            res = process_resume_for_position(cid, job_id, threshold, actor_id=actor_id, base_url=base_url)
            res.pop("state", None)
            _batch_update(run_id, idx, state=("Error" if res.get("error") else "Done"), **res)
        except Exception as exc:
            app.logger.exception("Batch item failed")
            _batch_update(run_id, idx, state="Error", error=str(exc))
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work, range(count)))
    finally:
        con = db()
        con.execute("UPDATE batch_runs SET status='Finished',finished_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), run_id))
        con.commit(); con.close()
