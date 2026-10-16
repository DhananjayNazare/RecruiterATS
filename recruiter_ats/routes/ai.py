"""AI screening, matching and interview-assistant endpoints."""
import json
from datetime import datetime
from flask import jsonify, render_template, request, session
from recruiter_ats.config import app
from recruiter_ats.database import db, set_setting
from recruiter_ats.security import audit, current_user, login_required, staff_only
from recruiter_ats.services.ai import AI_SWITCHES, ai_json, ai_threshold_default, position_jd_text, read_threshold
from recruiter_ats.services.files import clean_resume_text, summarize_resume_text
from recruiter_ats.services.mailer import get_smtp_config
from recruiter_ats.services.scheduling import get_base_url, sched_config
from recruiter_ats.services.screening import apply_screening_result


@app.route("/ai")
@staff_only
def ai_center():
    con=db(); jobs=con.execute("SELECT id,title FROM jobs WHERE status='Open' ORDER BY title").fetchall(); con.close()
    try: selected=int(request.args.get("position") or 0)
    except ValueError: selected=0
    smtp=get_smtp_config()
    return render_template("ai.html", jobs=jobs, positions=jobs, selected_position=selected, batch_threshold=ai_threshold_default(),
                           sched=sched_config(), smtp_ready=bool(smtp["host"] and smtp["from_email"]))

@app.route("/ai/resume/<int:cid>",methods=["POST"])
@staff_only
def ai_resume(cid):
    con=db(); c=con.execute("SELECT * FROM candidates WHERE id=?",(cid,)).fetchone(); con.close()
    if not c or not c["resume_text"]: return jsonify(ok=False,error="Candidate has no readable resume text.")
    payload=request.get_json(silent=True) or {}
    threshold=read_threshold(payload.get("threshold") or request.form.get("threshold"))
    resume_summary = summarize_resume_text(c["resume_text"], max_chars=3500)
    prompt=f"""Extract structured candidate data from this brief plain-text resume summary. Do not invent missing facts.
Schema: {{"name":"","email":"","phone":"","location":"","skills":[],"experience_years":0,"education":[],"certifications":[],"summary":"","strengths":[]}}
Rules: use only evidence in the resume; experience_years must be numeric; keep skills/certifications/strengths concise.
Resume summary:
{resume_summary}"""
    data,err=ai_json(prompt)
    if err: return jsonify(ok=False,error=err),503
    skills=data.get("skills",[]) if isinstance(data.get("skills",[]),list) else []
    education=data.get("education",[]) if isinstance(data.get("education",[]),list) else []
    certifications=data.get("certifications",[]) if isinstance(data.get("certifications",[]),list) else []
    strengths=data.get("strengths",[]) if isinstance(data.get("strengths",[]),list) else []
    con=db(); con.execute("""UPDATE candidates SET name=?,email=?,phone=?,location=?,skills=?,experience_years=?,education=?,certifications=?,ai_summary=?,ai_strengths=?,updated_at=? WHERE id=?""",
                          (data.get("name") or c["name"],data.get("email") or c["email"],data.get("phone") or c["phone"],
                           data.get("location") or c["location"],", ".join(map(str,skills)),float(data.get("experience_years") or 0),
                           ", ".join(map(str,education)),", ".join(map(str,certifications)),data.get("summary",""),", ".join(map(str,strengths)),datetime.now().isoformat(timespec="seconds"),cid))
    con.commit(); con.close()

    # Every analyzed resume is scored against every position (JD) this candidate is attached to.
    con=db(); apps=con.execute("""SELECT a.id,a.job_id,a.status,j.title,j.description,j.requirements,j.jd_text FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=? ORDER BY a.id DESC""",(cid,)).fetchall(); con.close()
    decisions=[]; actor=session.get("user_id"); base=get_base_url()
    for a in apps:
        jd=position_jd_text(a)
        match_prompt=f"""Evaluate this candidate resume against this JD using ONLY supplied evidence. Return ONLY JSON: {{"score":0,"matching_keywords":[],"missing_requirements":[],"why_match":[]}}. score must be 0-100. matching_keywords up to 5. missing_requirements up to 5. why_match exactly 3 concise reasons.
POSITION: {a['title']}
JD:
{jd[:16000]}
CANDIDATE RESUME:
{resume_summary}"""
        md,me=ai_json(match_prompt)
        if me:
            decisions.append({"application_id":a["id"],"job":a["title"],"score":None,"decision":"AI Error","error":me})
            continue
        try: score=max(0,min(100,int(float(md.get("score",0)))))
        except (TypeError,ValueError): score=0
        screening={"matching_keywords":md.get("matching_keywords",[]) if isinstance(md.get("matching_keywords",[]),list) else [],
                   "missing_requirements":md.get("missing_requirements",[]) if isinstance(md.get("missing_requirements",[]),list) else [],
                   "why_match":md.get("why_match",[]) if isinstance(md.get("why_match",[]),list) else []}
        out=apply_screening_result(a["id"],score,screening,threshold,actor_id=actor,base_url=base)
        decisions.append({**screening,"score":score,"decision":out["decision"],"threshold":threshold,"application_id":a["id"],"job":a["title"],"interview":out.get("interview")})
    audit("AI_RESUME_PARSE","CANDIDATE",cid,f"resume analyzed; threshold={threshold}; applications={len(apps)}")
    return jsonify(ok=True,data={**data,"threshold":threshold,"decisions":decisions})

@app.route("/ai/screen/<int:aid>",methods=["POST"])
@staff_only
def ai_screen(aid):
    con=db(); row=con.execute("""SELECT a.*,c.name candidate,c.resume_text,c.skills candidate_skills,c.experience_years,
                                      j.title job,j.description,j.requirements,j.jd_text
                               FROM applications a JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
                               WHERE a.id=?""",(aid,)).fetchone(); con.close()
    if not row: return jsonify(ok=False,error="Application not found."),404
    payload=request.get_json(silent=True) or {}
    threshold=read_threshold(payload.get("threshold") or request.form.get("threshold"))
    resume_summary = summarize_resume_text(row["resume_text"], max_chars=3500)
    prompt=f"""Screen this candidate against this job using only evidence in the supplied text.
Do NOT write a paragraph, explanation, recommendation, summary, or long-form response.
Return ONLY one strict JSON object with exactly these fields:
{{"score":0,"matching_keywords":["","",""]}}
Rules:
- score must be an integer from 0 to 100 representing the evidence-based match between the candidate and the job.
- matching_keywords must contain EXACTLY 3 short bullet-style strings showing the strongest matching keywords/skills/requirements.
- Use concise keyword phrases, not sentences.
- If fewer than three strong matches exist, use the closest supported keywords and do not invent facts.
- No markdown, no code fences, no extra keys, and no text outside the JSON object.
Candidate skills: {row["candidate_skills"]}
Candidate experience: {row["experience_years"]} years
Resume summary:
{resume_summary}
Position: {row["job"]}
Job description (JD): {position_jd_text(row)[:16000]}"""
    data,err=ai_json(prompt)
    if err: return jsonify(ok=False,error=err),503
    try:
        score=max(0,min(100,int(float(data.get("score",0)))))
    except (TypeError,ValueError):
        score=0
    keywords=data.get("matching_keywords",[])
    if not isinstance(keywords,list): keywords=[]
    keywords=[str(x).strip() for x in keywords if str(x).strip()]
    keywords=(keywords+["Not identified","Not identified","Not identified"])[:3]
    out=apply_screening_result(aid,score,{"matching_keywords":keywords},threshold,actor_id=session.get("user_id"),base_url=get_base_url())
    data={"score":score,"matching_keywords":keywords,"decision":out["decision"],"status":out["status"],"interview":out.get("interview")}
    audit("AI_SCREENING","APPLICATION",aid,f"score={score} {out['decision']}")
    return jsonify(ok=True,data=data)

@app.route("/ai/job/<int:jid>/analyze", methods=["POST"])
@staff_only
def ai_analyze_job(jid):
    con=db(); job=con.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); con.close()
    if not job: return jsonify(ok=False,error="Job not found."),404
    jd=position_jd_text(job)
    data,err=ai_json(f"""Analyze this job description for recruitment matching. Return ONLY JSON:\n{{"role_summary":"","must_have_skills":[],"nice_to_have_skills":[],"experience_requirements":"","education_requirements":"","certifications":[],"keywords":[],"location_requirements":"","scoring_guidance":""}}\nDo not invent requirements. Separate mandatory from preferred items.\nJOB TITLE: {job['title']}\nJOB DESCRIPTION:\n{jd[:20000]}""")
    if err: return jsonify(ok=False,error=err),503
    con=db(); con.execute("UPDATE jobs SET ai_jd_analysis=?,updated_at=? WHERE id=?",(json.dumps(data,ensure_ascii=False),datetime.now().isoformat(timespec="seconds"),jid)); con.commit(); con.close()
    audit("AI_JD_ANALYSIS","JOB",jid)
    return jsonify(ok=True,data=data)

@app.route("/ai/job/<int:jid>/find-matches", methods=["POST"])
@staff_only
def ai_find_job_matches(jid):
    payload=request.get_json(silent=True) or {}
    threshold=read_threshold(payload.get("threshold"))
    schedule=bool(payload.get("schedule",True))
    actor=session.get("user_id"); base=get_base_url()
    try: candidate_limit=int(payload.get("candidate_limit",10))
    except (TypeError,ValueError): candidate_limit=10
    if candidate_limit < 10:
        return jsonify(ok=False,error="Candidates to Match must be 10 or more."),400
    auto_move=bool(payload.get("auto_move",True))
    con=db(); job=con.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
    candidates=con.execute("SELECT * FROM candidates WHERE COALESCE(resume_text,'')<>'' ORDER BY id LIMIT ?",(candidate_limit,)).fetchall()
    con.close()
    if not job: return jsonify(ok=False,error="Job not found."),404
    if not candidates: return jsonify(ok=True,data={"threshold":threshold,"total_candidates":0,"matches":[]})
    jd=position_jd_text(job)
    results=[]
    # One compact AI evaluation per candidate keeps evidence tied to the actual resume and avoids hallucinated cross-candidate comparisons.
    for c in candidates:
        resume=summarize_resume_text(c["resume_text"],max_chars=3500)
        prompt=f"""Evaluate this candidate against this JD using ONLY supplied evidence. Return ONLY JSON: {{"score":0,"matching_keywords":[],"missing_requirements":[],"why_match":[],"recommendation":""}}. score is 0-100. matching_keywords: up to 5 concise phrases. missing_requirements: up to 5. why_match: exactly 3 concise evidence-based reasons. recommendation must be one of Strong Match, Match, Borderline, Not Recommended.\nJD TITLE: {job['title']}\nJD:\n{jd[:16000]}\nCANDIDATE: {c['name']}\nSKILLS: {c['skills'] or ''}\nEXPERIENCE: {c['experience_years'] or 0}\nEDUCATION: {c['education'] or ''}\nCERTIFICATIONS: {c['certifications'] if 'certifications' in c.keys() else ''}\nRESUME:\n{resume}"""
        data,err=ai_json(prompt)
        if err: continue
        try: score=max(0,min(100,int(float(data.get("score",0)))))
        except (TypeError,ValueError): score=0
        result={"candidate_id":c["id"],"candidate":c["name"],"email":c["email"],"phone":c["phone"],"score":score,
                "matching_keywords":data.get("matching_keywords",[]) if isinstance(data.get("matching_keywords",[]),list) else [],
                "missing_requirements":data.get("missing_requirements",[]) if isinstance(data.get("missing_requirements",[]),list) else [],
                "why_match":data.get("why_match",[]) if isinstance(data.get("why_match",[]),list) else [],
                "recommendation":data.get("recommendation","")}
        # Same AI recruitment bands everywhere; reaching the threshold auto-schedules the interview and emails the candidate.
        con=db(); now=datetime.now().isoformat(timespec="seconds")
        found=con.execute("SELECT id FROM applications WHERE candidate_id=? AND job_id=?",(c["id"],jid)).fetchone()
        if found:
            aid=found["id"]
        else:
            con.execute("INSERT INTO applications(candidate_id,job_id,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",(c["id"],jid,"Applied","AI JD Match",now,now))
            aid=con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.commit(); con.close()
        out=apply_screening_result(aid,score,{k:result[k] for k in ("matching_keywords","missing_requirements","why_match","recommendation")},
                                   threshold,actor_id=actor,base_url=base,schedule=schedule)
        result.update({"decision":out["decision"],"threshold":threshold,"application_id":aid,
                       "moved_to_interview":out["status"]=="Interview","interview":out.get("interview")})
        results.append(result)
    results.sort(key=lambda x:x["score"],reverse=True)
    audit("AI_JD_DATABASE_MATCH","JOB",jid,f"threshold={threshold};candidate_limit={candidate_limit};checked={len(candidates)};matches={len(results)}")
    return jsonify(ok=True,data={"threshold":threshold,"total_candidates":len(candidates),"requested_candidates":candidate_limit,"matches":results})

@app.route("/ai/application/<int:aid>/explain", methods=["POST"])
@staff_only
def ai_explain_application(aid):
    con=db(); row=con.execute("""SELECT a.*,c.name candidate,c.resume_text,c.skills,c.experience_years,c.education,c.email,c.phone,j.title job,j.description,j.requirements,j.jd_text FROM applications a JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id WHERE a.id=?""",(aid,)).fetchone(); con.close()
    if not row: return jsonify(ok=False,error="Application not found."),404
    data,err=ai_json(f"""Explain this candidate/JD match using only evidence. Return ONLY JSON: {{"score":0,"why_match":[],"matching_keywords":[],"missing_requirements":[],"experience_fit":"","recommendation":""}}. why_match must contain exactly 3 concise reasons.\nJOB: {row['job']}\nJD: {position_jd_text(row)[:18000]}\nCANDIDATE: {row['candidate']}\nSKILLS: {row['skills'] or ''}\nEXPERIENCE: {row['experience_years'] or 0}\nEDUCATION: {row['education'] or ''}\nRESUME: {summarize_resume_text(row['resume_text'],3500)}""")
    if err: return jsonify(ok=False,error=err),503
    return jsonify(ok=True,data=data)

@app.route("/ai/job/<int:jid>/questions", methods=["POST"])
@staff_only
def ai_job_questions(jid):
    con=db(); job=con.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); con.close()
    if not job: return jsonify(ok=False,error="Job not found."),404
    data,err=ai_json(f"""Create 8 JD-specific interview questions. Return ONLY JSON: {{"questions":[],"technical_questions":[],"behavioral_questions":[]}}. Questions must be concise, non-discriminatory, and directly tied to the role.\nTITLE: {job['title']}\nJOB DESCRIPTION: {position_jd_text(job)[:16000]}""")
    if err: return jsonify(ok=False,error=err),503
    con=db(); con.execute("UPDATE jobs SET ai_jd_questions=?,updated_at=? WHERE id=?",(json.dumps(data,ensure_ascii=False),datetime.now().isoformat(timespec="seconds"),jid)); con.commit(); con.close(); audit("AI_JD_INTERVIEW_QUESTIONS","JOB",jid)
    return jsonify(ok=True,data=data)

@app.route("/ai/match",methods=["POST"])
@staff_only
def ai_match():
    payload=request.get_json(silent=True) or {}
    jd=payload.get("job_description",""); resume=payload.get("resume","")
    if not jd or not resume: return jsonify(ok=False,error="Job description and resume are required.")
    resume_summary = summarize_resume_text(resume, max_chars=3500)
    jd_clean = clean_resume_text(jd)
    data,err=ai_json(f"""Compare this resume with this job description using only the supplied evidence.
Return {{"score":0,"matched_skills":[],"missing_skills":[],"transferable_skills":[],"experience_fit":"","summary":""}}.
JOB DESCRIPTION:
{jd_clean[:20000]}
RESUME SUMMARY:
{resume_summary}""")
    if err: return jsonify(ok=False,error=err),503
    return jsonify(ok=True,data=data)

@app.route("/ai/interview/<int:iid>",methods=["POST"])
@login_required
def ai_interview_assistant(iid):
    u=current_user()
    if u["role"]=="Candidate":
        return jsonify(ok=False,error="AI interview assistant is available to interviewers only."),403
    con=db()
    sql = """SELECT i.*,c.name candidate,c.resume_text,c.skills,c.experience_years,
                      j.title job,j.description job_description,j.requirements
               FROM interviews i JOIN applications a ON a.id=i.application_id
               JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id WHERE i.id=?"""
    args = (iid,)
    if u["role"] == "Interviewer":
        sql = sql[:-len(" WHERE i.id=?")] + " WHERE i.id=? AND (i.interviewer_user_id=? OR (i.interviewer_user_id IS NULL AND lower(trim(i.interviewer)) IN (lower(trim(?)),lower(trim(?)))))"
        args = (iid, u["id"], u["name"], u["email"])
    row=con.execute(sql,args).fetchone()
    con.close()
    if not row: return jsonify(ok=False,error="Interview not found."),404
    action=request.get_json(silent=True) or {}
    notes=(action.get("notes") or "").strip()
    mode=action.get("mode","questions")
    resume=summarize_resume_text(row["resume_text"],max_chars=3000)
    if mode=="questions":
        prompt=f"""Create 8 concise, role-specific interview questions for this candidate.
Return ONLY JSON: {{"questions":["..."]}}.
JOB: {row['job']}
DESCRIPTION: {row['job_description'] or ''}
REQUIREMENTS: {row['requirements'] or ''}
CANDIDATE RESUME: {resume}"""
    else:
        prompt=f"""Evaluate these interview notes against the job and candidate evidence.
Return ONLY JSON: {{"score":0,"strengths":[],"concerns":[],"follow_up_questions":[],"summary":""}}.
Score is 0-100 evidence-based and must not invent facts.
JOB: {row['job']}
DESCRIPTION: {row['job_description'] or ''}
CANDIDATE: {row['candidate']}
RESUME: {resume}
INTERVIEW NOTES: {notes[:12000]}"""
    data,err=ai_json(prompt)
    if err: return jsonify(ok=False,error=err),503
    con=db()
    if mode=="questions":
        con.execute("UPDATE interviews SET ai_questions=? WHERE id=?",(json.dumps(data,ensure_ascii=False),iid))
    else:
        con.execute("UPDATE interviews SET ai_feedback=? WHERE id=?",(json.dumps(data,ensure_ascii=False),iid))
    con.commit(); con.close()
    audit("AI_INTERVIEW_ASSISTANT","INTERVIEW",iid,mode)
    return jsonify(ok=True,data=data)


@app.route("/ai/switch",methods=["POST"])
@staff_only
def ai_switch():
    """Small AI ON/OFF buttons: choose whether AI decides a task itself or leaves it to the recruiter."""
    from recruiter_ats.security import has_permission
    u=current_user()
    if not (u["role"]=="Admin" or has_permission("manage_candidates")):
        return jsonify(ok=False,error="You do not have permission to change AI settings."),403
    key=request.form.get("key"); val="1" if request.form.get("value")=="1" else "0"
    if key not in AI_SWITCHES: return jsonify(ok=False,error="Unknown AI setting."),400
    set_setting(key,val); audit("UPDATE","AI_SWITCH",details=f"{key}={val}")
    return jsonify(ok=True,key=key,value=val)
