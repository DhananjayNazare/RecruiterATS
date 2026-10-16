"""Candidate Portal and Interviewer Portal pages."""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from flask import flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename
from recruiter_ats import config
from recruiter_ats.config import app
from recruiter_ats.database import db
from recruiter_ats.security import current_user, login_required, csrf_token
from recruiter_ats.services.files import allowed_file, extract_text, save_resume_upload, summarize_resume_text
from recruiter_ats.services.ai import ai_json, ai_configured, check_ai_connection
from recruiter_ats.services.ai_interview import AI_ROUND_NAME, decode_json
from recruiter_ats.services.ai_interview import ensure_ai_interview_invite
from recruiter_ats.security import audit


@app.route("/api/portal-ai-status")
@login_required
def portal_ai_status():
    """Return a minimal live AI connectivity status to candidate/interviewer portals.

    No keys, model configuration, endpoint URLs, or raw provider errors are exposed.
    """
    u = current_user()
    if u["role"] not in {"Candidate", "Interviewer"}:
        return jsonify(connected=False, configured=False, message="Portal status is only available to portal users."), 403
    configured = ai_configured()
    if not configured:
        return jsonify(connected=False, configured=False, message="AI is not configured. Please contact the recruitment team.")
    result = check_ai_connection()
    connected = bool(result.get("connected"))
    return jsonify(connected=connected, configured=True,
                   message=("AI interview service connected" if connected else
                            "AI is configured but the service is not currently reachable. Please try again later."))


@app.route("/candidate-panel")
@login_required
def candidate_panel():
    u = current_user()
    if u["role"] != "Candidate":
        flash("Candidate access required.", "danger")
        return redirect(url_for("dashboard"))
    con = db()
    candidate = None
    if u["candidate_id"]:
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
    if not candidate:
        candidate = con.execute("SELECT * FROM candidates WHERE lower(email)=lower(?) ORDER BY id DESC LIMIT 1", (u["email"],)).fetchone()
        if candidate:
            con.execute("UPDATE users SET candidate_id=? WHERE id=?", (candidate["id"], u["id"]))
            con.commit()

    # Open jobs are public to every signed-in candidate, even before a candidate
    # database profile has been linked/created.
    candidate_id = candidate["id"] if candidate else -1
    jobs = con.execute("""SELECT j.*,co.name company,l.name location,ca.name category,
                       a.id application_id,a.status application_status
                       FROM jobs j LEFT JOIN companies co ON co.id=j.company_id
                       LEFT JOIN locations l ON l.id=j.location_id LEFT JOIN categories ca ON ca.id=j.category_id
                       LEFT JOIN applications a ON a.job_id=j.id AND a.candidate_id=?
                       WHERE j.status='Open' ORDER BY j.id DESC""", (candidate_id,)).fetchall()

    applications = []
    candidate_interviews = []
    created_ai_invites = []
    if candidate:
        raw_apps = con.execute("""SELECT a.*,j.title job,j.description,j.requirements,j.employment_type,
                                l.name location,co.name company
                                FROM applications a JOIN jobs j ON j.id=a.job_id
                                LEFT JOIN locations l ON l.id=j.location_id
                                LEFT JOIN companies co ON co.id=j.company_id
                                WHERE a.candidate_id=? ORDER BY a.id DESC""", (candidate_id,)).fetchall()
        for a in raw_apps:
            d = dict(a)
            d["documents"] = decode_json(a["documents"], [])
            applications.append(d)
        # Repair missed invitations for candidates already selected by the recruiter or
        # by AI screening. Older builds and some automatic screening paths could update
        # application.status without creating the separate AI first-round invitation,
        # which left the candidate with no Set Time / Take Interview action.
        eligible_apps = con.execute("""SELECT id FROM applications
                                      WHERE candidate_id=? AND status IN ('Accepted','Shortlisted','Interview')
                                      ORDER BY id""", (candidate_id,)).fetchall()
        for eligible in eligible_apps:
            active_ai = con.execute("""SELECT id FROM interviews
                                      WHERE application_id=? AND round_name=?
                                        AND COALESCE(status,'')<>'Cancelled'
                                      ORDER BY id DESC LIMIT 1""",
                                    (eligible['id'], AI_ROUND_NAME)).fetchone()
            if not active_ai:
                invite_id, created = ensure_ai_interview_invite(con, eligible['id'])
                if created:
                    created_ai_invites.append((eligible['id'], invite_id))
        if created_ai_invites:
            con.commit()

        raw_interviews = con.execute("""SELECT i.*,j.title job FROM interviews i
                                  JOIN applications a ON a.id=i.application_id JOIN jobs j ON j.id=a.job_id
                                  WHERE a.candidate_id=? AND COALESCE(i.status,'')<>'Cancelled'
                                  ORDER BY COALESCE(i.scheduled_at,i.available_until,i.available_from)""",
                                  (candidate_id,)).fetchall()
        now = datetime.now()
        candidate_interviews = []
        for interview_row in raw_interviews:
            interview = dict(interview_row)
            interview['ai_can_schedule'] = False
            interview['ai_can_take'] = False
            interview['ai_schedule_expired'] = False
            interview['ai_open_at_display'] = ''
            if interview.get('round_name') == AI_ROUND_NAME:
                status = str(interview.get('status') or '')
                try:
                    until = datetime.fromisoformat(interview['available_until']) if interview.get('available_until') else None
                except (TypeError, ValueError):
                    until = None
                interview['ai_schedule_expired'] = bool(until and now > until)
                interview['ai_can_schedule'] = status == 'Awaiting Candidate' and not interview['ai_schedule_expired']
                if status in ('Scheduled', 'In Progress'):
                    try:
                        scheduled = datetime.fromisoformat(interview['scheduled_at']) if interview.get('scheduled_at') else now
                        opens_at = scheduled - timedelta(minutes=15)
                        interview['ai_open_at_display'] = opens_at.strftime('%d %b %Y, %I:%M %p')
                        interview['ai_can_take'] = status == 'In Progress' or (now >= opens_at and not interview['ai_schedule_expired'])
                    except (TypeError, ValueError):
                        interview['ai_can_take'] = status == 'In Progress'
            candidate_interviews.append(interview)
    con.close()
    for application_id, interview_id in created_ai_invites:
        audit('AI_INTERVIEW_INVITE_BACKFILLED', 'INTERVIEW', interview_id,
              f'Candidate portal repaired invitation for application {application_id}')
    stats = {
        "open_jobs": len(jobs),
        "applications": len(applications),
        "interviews": len(candidate_interviews),
    }
    # datetime-local only accepts minute precision; round up so its minimum is not
    # rejected by server-side validation because the current minute's seconds elapsed.
    portal_now = (datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat(timespec="minutes")
    return render_template("candidate_portal.html", candidate=candidate, jobs=jobs,
                           applications=applications, interviews=candidate_interviews, stats=stats,
                           portal_now=portal_now)


@app.route("/candidate-panel/apply/<int:jid>", methods=["POST"])
@login_required
def candidate_apply(jid):
    u = current_user()
    if u["role"] != "Candidate":
        return jsonify(ok=False, error="Candidate access required."), 403
    con = db()
    candidate = None
    if u["candidate_id"]:
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
    if not candidate:
        candidate = con.execute("SELECT * FROM candidates WHERE lower(email)=lower(?) ORDER BY id DESC LIMIT 1", (u["email"],)).fetchone()
    job = con.execute("SELECT id,title FROM jobs WHERE id=? AND status='Open'", (jid,)).fetchone()
    if not job:
        con.close()
        flash("This position is no longer open.", "danger")
        return redirect(url_for("candidate_panel"))

    resume = request.files.get("resume_file")
    needs_resume = not candidate or not candidate["resume_file"]
    if needs_resume and not (resume and resume.filename):
        con.close()
        flash("Upload a readable PDF, DOCX or TXT resume to apply for this role.", "danger")
        return redirect(url_for("candidate_panel"))

    now = datetime.now().isoformat(timespec="seconds")
    resume_stored = candidate["resume_file"] if candidate else ""
    resume_text = candidate["resume_text"] if candidate else ""
    resume_name = ""
    if resume and resume.filename:
        if not allowed_file(resume.filename):
            con.close(); flash("Resume must be a PDF, DOCX or TXT file.", "danger")
            return redirect(url_for("candidate_panel"))
        resume_name = secure_filename(resume.filename)
        resume_stored = f"{uuid.uuid4().hex}_{resume_name}"
        try:
            save_resume_upload(resume, config.UPLOAD_DIR / resume_stored)
        except ValueError as e:
            con.close(); flash(str(e), "danger"); return redirect(url_for("candidate_panel"))
        resume_text = extract_text(config.UPLOAD_DIR / resume_stored)
        if resume_text.startswith("[Resume extraction error:"):
            resume_text = ""

    # A self-registered candidate may not yet exist in the recruitment database.
    # Create/link the candidate at application time rather than blocking job browsing.
    if not candidate:
        cur = con.execute("""INSERT INTO candidates(name,email,resume_file,resume_text,created_at,updated_at)
                             VALUES(?,?,?,?,?,?)""",
                          (u["name"], u["email"], resume_stored or None, resume_text or None, now, now))
        candidate_id = cur.lastrowid
        con.execute("UPDATE users SET candidate_id=? WHERE id=?", (candidate_id, u["id"]))
    else:
        candidate_id = candidate["id"]
        if resume and resume.filename:
            con.execute("UPDATE candidates SET resume_file=?,resume_text=?,updated_at=? WHERE id=?",
                        (resume_stored, resume_text or None, now, candidate_id))

    docs = []
    if resume and resume.filename and resume_stored:
        docs.append({"name": resume_name, "file": resume_stored})
    added_files = []
    for f in request.files.getlist("documents"):
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            flash(f"'{f.filename}' skipped — only PDF, DOCX or TXT documents are accepted.", "warning")
            continue
        original = secure_filename(f.filename)
        stored = f"{uuid.uuid4().hex}_{original}"
        try:
            save_resume_upload(f, config.UPLOAD_DIR / stored)
        except ValueError as e:
            flash(f"{original}: {e}", "warning")
            continue
        docs.append({"name": original, "file": stored})
        added_files.append(stored)

    try:
        con.execute("""INSERT INTO applications(candidate_id,job_id,status,source,documents,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?)""",
                    (candidate_id, jid, "Applied", "Candidate Portal", json.dumps(docs, ensure_ascii=False) if docs else None, now, now))
        con.commit()
        audit("CANDIDATE_APPLY", "APPLICATION", con.execute("SELECT last_insert_rowid()").fetchone()[0], job["title"])
        flash("Application submitted. You can track recruiter updates from your account.", "success")
    except sqlite3.IntegrityError:
        con.rollback()
        flash("You have already applied for this job.", "warning")
    finally:
        con.close()
    return redirect(url_for("candidate_panel"))


@app.route("/candidate-panel/application/<int:aid>/documents", methods=["POST"])
@login_required
def candidate_upload_documents(aid):
    u=current_user()
    if u["role"] != "Candidate": return jsonify(ok=False,error="Candidate access required."),403
    con=db()
    c=con.execute("SELECT * FROM candidates WHERE id=? OR lower(email)=lower(?)", (u["candidate_id"] or -1,u["email"])).fetchone()
    a=con.execute("SELECT * FROM applications WHERE id=? AND candidate_id=?", (aid,c["id"] if c else -1)).fetchone()
    if not c or not a:
        con.close(); flash("Application not found.","danger"); return redirect(url_for("candidate_panel"))
    try: docs=json.loads(a["documents"]) if a["documents"] else []
    except Exception: docs=[]
    added=0
    for f in request.files.getlist("documents"):
        if f and f.filename:
            if not allowed_file(f.filename):
                flash(f"'{f.filename}' skipped — only PDF, DOCX or TXT files are accepted.","warning"); continue
            filename=f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
            f.save(config.UPLOAD_DIR/filename)
            docs.append({"name":f.filename,"file":filename}); added+=1
    if added:
        con.execute("UPDATE applications SET documents=?,updated_at=? WHERE id=?",
                     (json.dumps(docs),datetime.now().isoformat(timespec="seconds"),aid))
        con.commit(); flash(f"{added} document(s) uploaded.","success")
    con.close(); return redirect(url_for("candidate_panel"))

@app.route("/candidate-panel/document/<int:aid>/<path:fname>")
@login_required
def candidate_document(aid,fname):
    u=current_user()
    con=db()
    c=con.execute("SELECT * FROM candidates WHERE id=? OR lower(email)=lower(?)", (u["candidate_id"] or -1,u["email"])).fetchone() if u["role"]=="Candidate" else None
    if u["role"]=="Candidate":
        a=con.execute("SELECT documents FROM applications WHERE id=? AND candidate_id=?", (aid,c["id"] if c else -1)).fetchone()
    else:
        a=con.execute("SELECT documents FROM applications WHERE id=?", (aid,)).fetchone()
    con.close()
    if not a: return "Document not found",404
    try: docs=json.loads(a["documents"]) if a["documents"] else []
    except Exception: docs=[]
    if not any(d["file"]==fname for d in docs): return "Document not found",404
    return send_from_directory(config.UPLOAD_DIR, fname)

def _candidate_for_user(con, u):
    candidate = None
    if u["candidate_id"]:
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (u["candidate_id"],)).fetchone()
    if not candidate:
        candidate = con.execute("SELECT * FROM candidates WHERE lower(email)=lower(?) ORDER BY id DESC LIMIT 1", (u["email"],)).fetchone()
        if candidate:
            con.execute("UPDATE users SET candidate_id=? WHERE id=?", (candidate["id"], u["id"]))
            con.commit()
    return candidate


def _owned_ai_interview(con, u, iid):
    candidate = _candidate_for_user(con, u)
    if not candidate:
        return None
    return con.execute("""SELECT i.*,a.candidate_id,c.name candidate_name,c.email candidate_email,
                               c.resume_text,c.skills,c.experience_years,c.education,
                               j.title job,j.description job_description,j.requirements,j.jd_text
                        FROM interviews i JOIN applications a ON a.id=i.application_id
                        JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
                        WHERE i.id=? AND i.round_name=? AND a.candidate_id=?""",
                       (iid, AI_ROUND_NAME, candidate["id"])).fetchone()


@app.route("/candidate-panel/ai-interview/<int:iid>")
@login_required
def candidate_ai_interview(iid):
    u = current_user()
    if u["role"] != "Candidate":
        return "Candidate access required", 403
    con = db()
    row = _owned_ai_interview(con, u, iid)
    con.close()
    if not row:
        return "AI interview not found or access denied", 404
    questions_raw = decode_json(row["ai_questions"], {})
    if isinstance(questions_raw, dict):
        questions_raw = questions_raw.get("questions", [])
    if not isinstance(questions_raw, list):
        questions_raw = []
    questions = []
    for item in questions_raw:
        if isinstance(item, dict):
            questions.append({"question": str(item.get("question") or item.get("text") or ""),
                              "competency": str(item.get("competency") or "Role competency"),
                              "type": str(item.get("type") or "Role-specific")})
        else:
            questions.append({"question": str(item), "competency": "Role competency", "type": "Role-specific"})
    answers = decode_json(row["ai_answers"], {})
    tracking = decode_json(row["ai_tracking"], {})
    return render_template("candidate_ai_interview.html", interview=row, questions=questions,
                           answers=answers, tracking=tracking)


@app.route("/candidate-panel/ai-interview/<int:iid>/schedule", methods=["POST"])
@login_required
def candidate_schedule_ai_interview(iid):
    u = current_user()
    if u["role"] != "Candidate":
        return "Candidate access required", 403
    con = db()
    row = _owned_ai_interview(con, u, iid)
    if not row:
        con.close(); return "AI interview not found or access denied", 404
    if row["status"] != "Awaiting Candidate":
        con.close(); flash("This AI interview is no longer waiting for a time selection.", "warning")
        return redirect(url_for("candidate_panel"))
    raw_time = (request.form.get("scheduled_at") or "").strip()
    try:
        selected = datetime.strptime(raw_time, "%Y-%m-%dT%H:%M")
        window_start = datetime.fromisoformat(row["available_from"])
        window_end = datetime.fromisoformat(row["available_until"])
    except (ValueError, TypeError):
        con.close(); flash("Choose a valid interview date and time.", "danger")
        return redirect(url_for("candidate_panel"))
    now = datetime.now().replace(second=0, microsecond=0)
    if selected < now or selected < window_start.replace(second=0, microsecond=0) or selected > window_end:
        con.close(); flash("Select a time from now until the end of your 48-hour invitation window.", "danger")
        return redirect(url_for("candidate_panel"))
    con.execute("UPDATE interviews SET scheduled_at=?,status='Scheduled',mode='AI',interviewer='AI Interviewer' WHERE id=?",
                (selected.isoformat(timespec="minutes"), iid))
    con.commit(); con.close()
    audit("AI_INTERVIEW_TIME_SELECTED", "INTERVIEW", iid, selected.isoformat(timespec="minutes"))
    flash("Your AI interview time is confirmed. The interview page is separate from any external meeting link.", "success")
    return redirect(url_for("candidate_panel"))


@app.route("/candidate-panel/ai-interview/<int:iid>/start", methods=["POST"])
@login_required
def candidate_ai_interview_start(iid):
    u = current_user()
    if u["role"] != "Candidate":
        return "Candidate access required", 403
    con = db()
    row = _owned_ai_interview(con, u, iid)
    if not row:
        con.close(); return "AI interview not found or access denied", 404
    now = datetime.now()
    if row["status"] == "Completed":
        con.close(); flash("This AI interview has already been submitted.", "info")
        return redirect(url_for("candidate_ai_interview", iid=iid))
    if row["status"] not in ("Scheduled", "In Progress"):
        con.close(); flash("Choose an interview time from your Candidate Portal first.", "warning")
        return redirect(url_for("candidate_panel"))
    if request.form.get("camera_consent") != "yes" and row["status"] != "In Progress":
        con.close(); flash("Please review and acknowledge the interview and camera-tracking notice before starting.", "warning")
        return redirect(url_for("candidate_ai_interview", iid=iid))
    try:
        opens_at = datetime.fromisoformat(row["scheduled_at"]) - timedelta(minutes=15)
    except Exception:
        opens_at = now
    if now < opens_at:
        con.close(); flash("Your AI interview opens 15 minutes before your selected time.", "warning")
        return redirect(url_for("candidate_ai_interview", iid=iid))
    if row["available_until"] and now > datetime.fromisoformat(row["available_until"]):
        con.close(); flash("The 48-hour AI interview window has expired. Contact the recruiter to reopen it.", "danger")
        return redirect(url_for("candidate_panel"))

    question_payload = decode_json(row["ai_questions"], {})
    existing_questions = question_payload.get("questions", []) if isinstance(question_payload, dict) else question_payload
    if len(existing_questions or []) < 40:
        resume = summarize_resume_text(row["resume_text"], max_chars=2600)
        jd = (row["jd_text"] or "") + "\n" + (row["job_description"] or "") + "\n" + (row["requirements"] or "")
        prompt = f"""Create EXACTLY 40 distinct, concise AI first-round interview questions for this candidate and job.
Return only JSON in this format: {{"questions":[{{"question":"...","type":"Technical|Behavioral|Situational|Experience","competency":"..."}}]}}.
Include a balanced mix of technical/job-specific, practical scenarios, troubleshooting, past project evidence, problem-solving, communication and behavioral questions relevant to the job. Questions must be fair and non-discriminatory; never ask about protected or irrelevant personal characteristics. Do not provide answers.
JOB TITLE: {row['job']}
JOB DESCRIPTION AND REQUIREMENTS: {jd[:12000]}
CANDIDATE RESUME TEXT: {resume[:2800]}
The questions must refer to the role and resume evidence where appropriate. Return exactly 40 items, numbered only by their array order."""
        data, err = ai_json(prompt)
        raw_questions = data.get("questions", []) if isinstance(data, dict) else []
        normalised = []
        for item in raw_questions if isinstance(raw_questions, list) else []:
            if isinstance(item, dict):
                text = str(item.get("question") or item.get("text") or "").strip()
                kind = str(item.get("type") or "Role-specific").strip()
                competency = str(item.get("competency") or "Role competency").strip()
            else:
                text, kind, competency = str(item).strip(), "Role-specific", "Role competency"
            if text and text not in [x["question"] for x in normalised]:
                normalised.append({"question": text, "type": kind, "competency": competency})
            if len(normalised) == 40:
                break
        if err or not normalised:
            con.close()
            flash("The AI question set could not be generated. Check the AI connection in Admin Settings and try again." + (f" Details: {err[:220]}" if err else ""), "danger")
            return redirect(url_for("candidate_ai_interview", iid=iid))
        # Preserve exactly 40 questions even when a configured AI endpoint returns a
        # partial list. Supplements are varied, job-specific prompts and avoid duplicates.
        fallback_prompts = [
            f"Describe a typical task in a {row['job']} role and explain how you would complete it from requirements to verification.",
            f"Which tools or systems would you rely on most in a {row['job']} role, and why?",
            f"Walk through a challenging problem relevant to {row['job']} work and explain your troubleshooting sequence.",
            f"How would you prioritize three urgent tasks with competing deadlines in this role?",
            f"Describe a project related to {row['job']} and clarify your individual contribution.",
            f"What measurable result would demonstrate that you completed a {row['job']} task successfully?",
            f"How do you verify the accuracy and quality of your work before handing it over?",
            f"Tell us about a time you had to learn a new tool quickly to complete a work task.",
            f"How would you explain a technical or complex issue to a non-specialist stakeholder?",
            f"Describe a work mistake or failed approach and what you changed afterward.",
            f"What information do you gather before deciding how to solve an unfamiliar problem?",
            f"How would you document a process so another teammate could repeat it reliably?",
            f"Describe how you handle a blocker when the person who can help is unavailable.",
            f"How do you balance speed and correctness when a deadline is close?",
            f"Give an example of feedback you received and how you applied it in later work.",
            f"What risks would you check before implementing a significant change in this role?",
            f"How would you test a solution before releasing it to users or stakeholders?",
            f"Describe how you communicate progress when a task takes longer than expected.",
            f"What would you do if the stated requirement appeared unclear or contradictory?",
            f"How would you approach a recurring issue when the immediate workaround keeps failing?",
            f"Explain how you decide when to investigate further and when to escalate an issue.",
            f"What evidence would you collect to show that a suspected root cause is correct?",
            f"Describe how you protect confidential information while carrying out this role.",
            f"How would you plan your first two weeks to become productive in a {row['job']} position?",
            f"What dependencies would you confirm before starting a cross-team assignment?",
            f"How would you respond if a teammate disagreed with your proposed solution?",
            f"Describe a situation where you had to adapt your plan because priorities changed.",
            f"How do you keep your skills current for the tools and practices used in this field?",
            f"Which part of the job description aligns most closely with your demonstrated experience?",
            f"Which requirement would require the most learning for you, and how would you address it?",
            f"How would you break a complex assignment into manageable and verifiable steps?",
            f"What checks would you perform before handing a deliverable to a customer or colleague?",
            f"Describe a time when careful analysis helped you avoid a larger problem.",
            f"How would you handle an unexpected production or business issue while preserving a clear record of actions?",
            f"What would you include in a concise handover note after finishing a task?",
            f"How do you decide which details belong in documentation and which belong in a summary?",
            f"How would you investigate a problem that cannot be reproduced consistently?",
            f"What would you do if your initial solution improved one metric but worsened another?",
            f"Describe how you would measure whether a process improvement actually helped.",
            f"What questions would you ask the hiring team to clarify success expectations for this role?",
            f"What is one example from your resume that best demonstrates your readiness for this position?",
            f"How would you ensure a repeated task is performed consistently each time?",
            f"Explain how you would respond to a request that conflicts with an established safety or security rule.",
            f"What would you do if you discovered that a report or record contained inconsistent information?",
            f"How would you approach collaboration when ownership of a task is shared across teams?",
            f"What would you review after completing a task to identify opportunities for improvement?",
        ]
        seen_questions = {item["question"].casefold() for item in normalised}
        for fallback_question in fallback_prompts:
            if len(normalised) >= 40:
                break
            if fallback_question.casefold() in seen_questions:
                continue
            normalised.append({"question": fallback_question, "type": "Situational", "competency": "Applied role judgement"})
            seen_questions.add(fallback_question.casefold())
        final_topics = [
            "validate assumptions before work starts", "handle an urgent customer escalation", "compare two possible solutions",
            "protect data quality across a handoff", "recover from an interrupted task", "check dependencies before a release",
            "investigate a performance regression", "report a risk early", "review an unfamiliar technical design",
            "confirm acceptance criteria with stakeholders", "monitor a change after implementation", "learn from a near miss",
        ]
        topic_index = 0
        while len(normalised) < 40:
            topic = final_topics[topic_index % len(final_topics)]
            fallback_question = f"As a {row['job']} professional, explain how you would {topic} (scenario {topic_index + 1})."
            topic_index += 1
            if fallback_question.casefold() in seen_questions:
                continue
            normalised.append({"question": fallback_question, "type": "Situational", "competency": "Applied role judgement"})
            seen_questions.add(fallback_question.casefold())
        con.execute("UPDATE interviews SET ai_questions=?,ai_answers=COALESCE(ai_answers,'{}'),status='In Progress',ai_started_at=COALESCE(ai_started_at,?) WHERE id=?",
                    (json.dumps({"questions": normalised}, ensure_ascii=False), now.isoformat(timespec="seconds"), iid))
    else:
        con.execute("UPDATE interviews SET status='In Progress',ai_started_at=COALESCE(ai_started_at,?) WHERE id=?",
                    (now.isoformat(timespec="seconds"), iid))
    con.commit(); con.close()
    audit("AI_INTERVIEW_STARTED", "INTERVIEW", iid, "Candidate started AI first-round interview")
    return redirect(url_for("candidate_ai_interview", iid=iid))


@app.route("/candidate-panel/ai-interview/<int:iid>/answer", methods=["POST"])
@login_required
def candidate_ai_interview_answer(iid):
    u = current_user()
    if u["role"] != "Candidate":
        return jsonify(ok=False, error="Candidate access required."), 403
    body = request.get_json(silent=True) or {}
    try:
        q_index = int(body.get("question_index"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Question number is invalid."), 400
    answer_text = str(body.get("answer") or "").strip()[:5000]
    answer_mode = "voice" if body.get("answer_mode") == "voice" else "typed"
    con = db()
    row = _owned_ai_interview(con, u, iid)
    if not row:
        con.close(); return jsonify(ok=False, error="AI interview not found."), 404
    if row["status"] != "In Progress":
        con.close(); return jsonify(ok=False, error="This interview is not accepting answers."), 409
    q_data = decode_json(row["ai_questions"], {})
    q_list = q_data.get("questions", []) if isinstance(q_data, dict) else q_data
    if q_index < 0 or q_index >= len(q_list):
        con.close(); return jsonify(ok=False, error="Question number is out of range."), 400
    existing = decode_json(row["ai_answers"], {})
    if not isinstance(existing, dict): existing = {}
    q = q_list[q_index]
    question_text = q.get("question", "") if isinstance(q, dict) else str(q)
    existing[str(q_index)] = {"question": question_text, "answer": answer_text,
                              "answer_mode": answer_mode, "saved_at": datetime.now().isoformat(timespec="seconds")}
    con.execute("UPDATE interviews SET ai_answers=? WHERE id=?", (json.dumps(existing, ensure_ascii=False), iid))
    con.commit(); con.close()
    return jsonify(ok=True, saved=True, answered=sum(1 for a in existing.values() if str(a.get("answer") or "").strip()))


@app.route("/candidate-panel/ai-interview/<int:iid>/submit", methods=["POST"])
@login_required
def candidate_ai_interview_submit(iid):
    u = current_user()
    if u["role"] != "Candidate":
        return jsonify(ok=False, error="Candidate access required."), 403
    body = request.get_json(silent=True) or {}
    raw_tracking = body.get("tracking") if isinstance(body.get("tracking"), dict) else {}
    tracking = {}
    for key in ("face_missing", "look_away", "head_movement", "samples", "camera_seconds"):
        try: tracking[key] = max(0, min(1000000, int(float(raw_tracking.get(key, 0)))))
        except (TypeError, ValueError): tracking[key] = 0
    con = db()
    row = _owned_ai_interview(con, u, iid)
    if not row:
        con.close(); return jsonify(ok=False, error="AI interview not found."), 404
    if row["status"] == "Completed":
        con.close(); return jsonify(ok=True, already_submitted=True, redirect=url_for("candidate_panel"))
    if row["status"] != "In Progress":
        con.close(); return jsonify(ok=False, error="Start the AI interview before submitting."), 409
    q_data = decode_json(row["ai_questions"], {})
    q_list = q_data.get("questions", []) if isinstance(q_data, dict) else q_data
    answers = decode_json(row["ai_answers"], {})
    if not isinstance(answers, dict): answers = {}
    answer_items = []
    for index, question in enumerate(q_list):
        question_text = question.get("question", "") if isinstance(question, dict) else str(question)
        ans = answers.get(str(index), {})
        answer_items.append({"question_number": index + 1, "question": question_text,
                             "competency": question.get("competency", "") if isinstance(question, dict) else "",
                             "type": question.get("type", "") if isinstance(question, dict) else "",
                             "answer": str(ans.get("answer") or "")[:2200],
                             "answer_mode": ans.get("answer_mode", "typed")})
    nonempty = sum(1 for item in answer_items if item["answer"].strip())
    prompt = f"""Evaluate this candidate's completed first-round interview against the job and resume evidence.
Return ONLY JSON with this schema:
{{"overall_score":0,"recommendation":"Advance|Hold|Not Recommended","executive_summary":"","role_fit":"","competency_scores":[{{"competency":"","score":0,"evidence":""}}],"strengths":[""],"development_areas":[""],"question_analysis":[{{"question_number":1,"question":"","answer_quality_score":0,"answer_summary":"","positive_evidence":[""],"gaps":[""],"follow_up":""}}],"role_specific_skills":[{{"skill":"","evidence":"","level":"Strong|Moderate|Limited|Not demonstrated"}}],"communication_assessment":"","unanswered_question_numbers":[],"next_round_questions":[""],"recruiter_notes":""}}.
Score only the relevance, clarity, completeness and evidence in the candidate's text answers, compared with the supplied job requirements. Do not invent candidate facts. Missing answers must be noted as unanswered, not treated as proof of inability. Keep feedback job-related, explain scores with evidence and avoid protected-class or personal-characteristic inferences. The voice answers are browser-transcribed text, so do not judge accent, voice quality or vocal emotion. Browser camera attention counts are approximate technical observations only: do not use them to infer honesty, personality, competence, or change the interview score; report them separately for human context.
JOB: {row['job']}
JOB DESCRIPTION: {(row['jd_text'] or '')[:7000]}\n{(row['job_description'] or '')[:7000]}
REQUIREMENTS: {(row['requirements'] or '')[:4000]}
CANDIDATE: {row['candidate_name']}
RESUME EVIDENCE: {summarize_resume_text(row['resume_text'], max_chars=2600)}
ANSWERED: {nonempty} of {len(answer_items)} questions.
ANSWERS: {json.dumps(answer_items, ensure_ascii=False)[:30000]}"""
    report, err = ai_json(prompt)
    if err or not isinstance(report, dict):
        con.close()
        return jsonify(ok=False, error="AI report generation failed; answers are saved. Please retry submission." + (f" Details: {err[:220]}" if err else "")), 503
    report["candidate_camera_observations"] = {
        **tracking,
        "tracking_enabled": tracking["samples"] > 0,
        "interpretation": ("Approximate browser-side gaze/head movement signals. They are not a measure of honesty, ability, or job competence and were not used in the AI score." if tracking["samples"] > 0 else "Browser camera tracking was not available or was not enabled. No video was stored or sent to the server."),
    }
    report["interview_metadata"] = {
        "job": row["job"], "candidate": row["candidate_name"],
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "questions_total": len(answer_items), "questions_answered": nonempty,
        "questions_unanswered": len(answer_items) - nonempty,
    }
    now_text = datetime.now().isoformat(timespec="seconds")
    con.execute("UPDATE interviews SET ai_answers=?,ai_tracking=?,ai_report=?,ai_feedback=?,status='Completed',ai_completed_at=? WHERE id=?",
                (json.dumps(answers, ensure_ascii=False), json.dumps(tracking), json.dumps(report, ensure_ascii=False),
                 json.dumps(report, ensure_ascii=False), now_text, iid))
    con.commit(); con.close()
    audit("AI_INTERVIEW_SUBMITTED", "INTERVIEW", iid, f"AI report generated; {nonempty}/{len(answer_items)} questions answered")
    return jsonify(ok=True, report_submitted=True, redirect=url_for("candidate_panel"))


@app.route("/applications/<int:aid>/ai-interview-report")
@login_required
def ai_interview_report(aid):
    u = current_user()
    if u["role"] in {"Candidate", "Interviewer"}:
        return "Recruiter access required", 403
    con = db()
    row = con.execute("""SELECT i.*,a.id application_id,c.name candidate,c.email,j.title job,j.description job_description
                        FROM interviews i JOIN applications a ON a.id=i.application_id
                        JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
                        WHERE a.id=? AND i.round_name=? ORDER BY i.id DESC LIMIT 1""",
                      (aid, AI_ROUND_NAME)).fetchone()
    con.close()
    if not row or not row["ai_report"]:
        flash("No AI interview report is available for this application yet.", "warning")
        return redirect(url_for("applications"))
    report = decode_json(row["ai_report"], {})
    answers = decode_json(row["ai_answers"], {})
    q_data = decode_json(row["ai_questions"], {})
    q_list = q_data.get("questions", []) if isinstance(q_data, dict) else q_data
    detail = []
    for index, question in enumerate(q_list):
        qtext = question.get("question", "") if isinstance(question, dict) else str(question)
        ans = answers.get(str(index), {}) if isinstance(answers, dict) else {}
        detail.append({"number": index + 1, "question": qtext, "answer": ans.get("answer", ""),
                       "answer_mode": ans.get("answer_mode", "typed")})
    return render_template("ai_interview_report.html", interview=row, report=report, answers=detail)


@app.route("/interviewer-panel")
@login_required
def interviewer_panel():
    u=current_user()
    if u["role"] != "Interviewer":
        flash("Interviewer access required.","danger"); return redirect(url_for("dashboard"))
    con=db()
    rows=con.execute("""SELECT i.*,c.id candidate_id,c.name candidate,c.email candidate_email,c.phone candidate_phone,c.location candidate_location,
                              c.skills,c.experience_years,c.education,c.resume_file,c.resume_text,c.photo,j.title job,j.description job_description,
                              j.requirements,co.name company,l.name location
                       FROM interviews i JOIN applications a ON a.id=i.application_id JOIN candidates c ON c.id=a.candidate_id
                       JOIN jobs j ON j.id=a.job_id LEFT JOIN companies co ON co.id=j.company_id LEFT JOIN locations l ON l.id=j.location_id
                       WHERE (i.interviewer_user_id=? OR (i.interviewer_user_id IS NULL AND lower(trim(i.interviewer)) IN (lower(trim(?)),lower(trim(?)))))
                       ORDER BY i.scheduled_at""", (u["id"],u["name"],u["email"])).fetchall()
    con.close()
    stats={"total":len(rows),
           "scheduled":sum(1 for r in rows if (r["status"] or "").lower()=="scheduled"),
           "completed":sum(1 for r in rows if (r["status"] or "").lower()=="completed")}
    return render_template("interviewer_portal.html",interviews=rows,stats=stats)

@app.route("/interviewer-panel/interview/<int:iid>")
@login_required
def interviewer_interview(iid):
    u=current_user()
    if u["role"] != "Interviewer": return "Interviewer access required",403
    con=db()
    row=con.execute("""SELECT i.*,c.*,c.id candidate_id,j.title job,j.description job_description,j.requirements,co.name company,l.name location
                      FROM interviews i JOIN applications a ON a.id=i.application_id JOIN candidates c ON c.id=a.candidate_id
                      JOIN jobs j ON j.id=a.job_id LEFT JOIN companies co ON co.id=j.company_id LEFT JOIN locations l ON l.id=j.location_id
                      WHERE i.id=? AND (i.interviewer_user_id=? OR (i.interviewer_user_id IS NULL AND lower(trim(i.interviewer)) IN (lower(trim(?)),lower(trim(?)))))""", (iid,u["id"],u["name"],u["email"])).fetchone()
    con.close()
    if not row: return "Interview not found or access denied",404
    return render_template("interviewer_interview.html", interview=row)

@app.route("/interviewer-panel/interview/<int:iid>/ai-assistant")
@login_required
def interviewer_ai_assistant_page(iid):
    u = current_user()
    if u["role"] != "Interviewer":
        return "Interviewer access required", 403
    con = db()
    row = con.execute("""SELECT i.*,c.id candidate_id,c.name candidate,c.email candidate_email,
                               c.resume_text,c.skills,c.experience_years,c.education,c.resume_file,
                               j.title job,j.description job_description,j.requirements,co.name company,l.name location
                        FROM interviews i JOIN applications a ON a.id=i.application_id JOIN candidates c ON c.id=a.candidate_id
                        JOIN jobs j ON j.id=a.job_id LEFT JOIN companies co ON co.id=j.company_id LEFT JOIN locations l ON l.id=j.location_id
                        WHERE i.id=? AND (i.interviewer_user_id=? OR
                          (i.interviewer_user_id IS NULL AND lower(trim(i.interviewer)) IN (lower(trim(?)),lower(trim(?)))))""",
                       (iid, u["id"], u["name"], u["email"])).fetchone()
    con.close()
    if not row:
        return "Interview not found or access denied", 404
    return render_template("interviewer_ai_assistant.html", interview=row)


@app.route("/interviewer-panel/resume/<int:cid>")
@login_required
def interviewer_resume(cid):
    u=current_user()
    if u["role"] != "Interviewer":
        return "Interviewer access required",403
    con=db()
    row=con.execute("""SELECT c.resume_file FROM candidates c
        WHERE c.id=? AND EXISTS (
          SELECT 1 FROM interviews i JOIN applications a ON a.id=i.application_id
          WHERE a.candidate_id=c.id AND (i.interviewer_user_id=? OR
            (i.interviewer_user_id IS NULL AND lower(trim(i.interviewer)) IN (lower(trim(?)),lower(trim(?)))))
        )""", (cid,u["id"],u["name"],u["email"])).fetchone()
    con.close()
    if not row or not row["resume_file"]:
        return "Resume not available",404
    path=config.UPLOAD_DIR/row["resume_file"]
    if not path.exists():
        return "Resume file not found",404
    return send_from_directory(config.UPLOAD_DIR, row["resume_file"], as_attachment=False)
