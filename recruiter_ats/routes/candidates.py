"""Candidates and applications: CRUD, batch resume upload, bulk email, PDF scan."""
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from flask import flash, jsonify, redirect, render_template, request, session, url_for
from pathlib import Path
from werkzeug.utils import secure_filename
from recruiter_ats import config
from recruiter_ats.config import app
from recruiter_ats.database import db
from recruiter_ats.security import audit, current_user, has_permission, login_required, permission_required, staff_only
from recruiter_ats.services.ai import ai_threshold_default, read_threshold
from recruiter_ats.services.files import allowed_file, allowed_image, compress_candidate_photo, extract_text, save_resume_upload
from recruiter_ats.services.mailer import send_bulk_email_bcc
from recruiter_ats.services.scheduling import get_base_url
from recruiter_ats.services.screening import run_batch
from recruiter_ats.services.ai_interview import ensure_ai_interview_invite


@app.route("/candidates")
@staff_only
def candidates():
    q=(request.args.get("q") or "").strip()
    try: pos=int(request.args.get("position") or 0)
    except ValueError: pos=0
    try: score=max(0,min(100,int(float(request.args.get("score") or 0))))       # minimum AI match score (%)
    except ValueError: score=0
    try: shown=max(1,min(500,int(request.args.get("shown") or 10)))   # first 10; the recruiter asks for more
    except ValueError: shown=10
    where="""(:q='' OR c.name LIKE :like OR c.email LIKE :like OR c.phone LIKE :like OR c.skills LIKE :like OR c.location LIKE :like
               OR EXISTS(SELECT 1 FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=c.id AND j.title LIKE :like))
          AND (:pos=0 OR EXISTS(SELECT 1 FROM applications a WHERE a.candidate_id=c.id AND a.job_id=:pos))
          AND (:score=0 OR EXISTS(SELECT 1 FROM applications a WHERE a.candidate_id=c.id AND a.match_score>=:score
                                  AND (:pos=0 OR a.job_id=:pos)))"""
    con=db()
    args={"q":q,"like":f"%{q}%","pos":pos,"score":score}
    total=con.execute(f"SELECT COUNT(*) FROM candidates c WHERE {where}",args).fetchone()[0]
    # only the columns the list shows (not resume_text), and only one page of rows, so the page stays small and fast
    rows=con.execute(f"""SELECT c.id,c.name,c.email,c.phone,c.location,c.photo,c.experience_years,
        (SELECT GROUP_CONCAT(j.title, ' • ') FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.candidate_id=c.id) positions,
        (SELECT MAX(a.match_score) FROM applications a WHERE a.candidate_id=c.id) best_score
        FROM candidates c WHERE {where}
        ORDER BY c.id DESC LIMIT :lim""",{**args,"lim":shown}).fetchall()
    positions=con.execute("SELECT id,title FROM jobs ORDER BY title").fetchall(); con.close()
    return render_template("candidates.html",candidates=rows,positions=positions,q=q,selected_position=pos,
                           batch_threshold=ai_threshold_default(),shown=shown,total=total,score=score)

@app.route("/candidates/add",methods=["GET","POST"])
@permission_required("manage_candidates")
def add_candidate():
    if request.method=="POST":
        name=request.form["name"].strip(); email=request.form.get("email","").strip()
        f=request.files.get("resume"); filename=""; text=""
        if f and f.filename:
            if not allowed_file(f.filename): flash("Only PDF, DOCX and TXT resumes are allowed.","danger"); return render_template("candidate_form.html")
            filename=f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"; p=config.UPLOAD_DIR/filename
            try:
                save_resume_upload(f, p)
            except ValueError as e:
                flash(str(e), "danger"); return render_template("candidate_form.html")
            text=extract_text(p)
        photo_file=request.files.get("photo"); photo=""
        if photo_file and photo_file.filename:
            if not allowed_image(photo_file.filename): flash("Photo must be PNG, JPG, WEBP or GIF.","danger"); return render_template("candidate_form.html")
            photo=f"{uuid.uuid4().hex}_candidate.jpg"; compress_candidate_photo(photo_file, photo)
        con=db(); now=datetime.now().isoformat(timespec="seconds")
        con.execute("""INSERT INTO candidates(name,email,phone,location,resume_file,resume_text,skills,experience_years,education,photo,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name,email,request.form.get("phone",""),request.form.get("location",""),filename,text,
                     request.form.get("skills",""),float(request.form.get("experience_years") or 0),request.form.get("education",""),photo,now,now))
        cid=con.execute("SELECT last_insert_rowid()").fetchone()[0]; con.commit(); con.close()
        audit("CREATE","CANDIDATE",cid); flash("Candidate added.","success"); return redirect(url_for("candidates"))
    return render_template("candidate_form.html")

@app.route("/candidates/batch-upload", methods=["POST"])
@login_required
def batch_upload_resumes():
    """Save up to 10 resumes. When a Position is selected, the full workflow then runs in the background for
    every resume: AI parse -> attach to the position's JD -> score -> auto-schedule interview -> email candidate."""
    if not has_permission("manage_candidates"):
        return jsonify(ok=False, error="Candidate management permission is required."), 403
    files = [f for f in request.files.getlist("resumes") if f and f.filename]
    if not files:
        return jsonify(ok=False, error="No resume files selected."), 400
    if len(files) > 10:
        return jsonify(ok=False, error="Maximum 10 resumes can be processed at once."), 400
    job_id = None
    if request.form.get("job_id"):
        con = db(); job = con.execute("SELECT id FROM jobs WHERE id=?", (request.form.get("job_id"),)).fetchone(); con.close()
        if not job:
            return jsonify(ok=False, error="The selected position was not found."), 400
        job_id = job["id"]
    threshold = read_threshold(request.form.get("threshold"))

    con = db(); now = datetime.now().isoformat(timespec="seconds")
    created=[]; errors=[]
    for f in files:
        if not allowed_file(f.filename):
            errors.append(f"{f.filename}: only PDF, DOCX and TXT files are allowed")
            continue
        safe = secure_filename(f.filename)
        stem = Path(safe).stem.replace("_", " ").replace("-", " ").strip()
        name = re.sub(r"\s+", " ", stem) or "Candidate"
        filename=f"{uuid.uuid4().hex}_{safe}"
        path=config.UPLOAD_DIR/filename
        try:
            save_resume_upload(f, path)
            text=extract_text(path)
            con.execute("""INSERT INTO candidates(name,email,phone,location,resume_file,resume_text,skills,experience_years,education,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (name,"","","",filename,text,"",0,"",now,now))
            cid=con.execute("SELECT last_insert_rowid()").fetchone()[0]
            created.append({"id":cid,"name":name,"filename":safe})
        except Exception as e:
            try: path.unlink(missing_ok=True)
            except Exception: pass
            errors.append(f"{f.filename}: {e}")
    con.commit()
    run_id = ""
    if job_id and created:
        run_id = uuid.uuid4().hex[:16]
        items = [{"candidate_id":x["id"],"name":x["name"],"file":x["filename"],"state":"Queued"} for x in created]
        con.execute("INSERT INTO batch_runs(id,job_id,status,total,done,created_by,created_at,results) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id,job_id,"Running",len(items),0,session.get("user_id"),now,json.dumps(items,ensure_ascii=False)))
        con.commit()
    con.close()
    for item in created:
        audit("CREATE","CANDIDATE",item["id"],item["name"])
    if run_id:
        threading.Thread(target=run_batch, args=(run_id, job_id, threshold, session.get("user_id"), get_base_url()),
                         daemon=True, name=f"batch-{run_id}").start()
    return jsonify(ok=True, created=created, errors=errors, count=len(created), run_id=run_id, screening=bool(run_id))

@app.route("/candidates/batch-status/<run_id>")
@staff_only
def batch_status(run_id):
    con=db(); row=con.execute("SELECT * FROM batch_runs WHERE id=?",(run_id,)).fetchone()
    pos=con.execute("SELECT title FROM jobs WHERE id=?",(row["job_id"],)).fetchone() if row else None
    con.close()
    if not row: return jsonify(ok=False,error="Batch run not found."),404
    return jsonify(ok=True,status=row["status"],total=row["total"],done=row["done"],position=(pos["title"] if pos else ""),
                   items=json.loads(row["results"] or "[]"))

@app.route("/candidates/bulk-email", methods=["POST"])
@login_required
def bulk_candidate_email():
    """Send one email to multiple existing candidates using BCC."""
    u = current_user()
    if not (u["role"] == "Admin" or has_permission("send_emails")):
        flash("You don't have permission to send candidate emails.", "danger")
        return redirect(url_for("candidates"))

    raw_ids = request.form.getlist("candidate_ids")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("message", "").strip()
    if not raw_ids:
        flash("Select at least one candidate.", "warning")
        return redirect(url_for("candidates"))
    if not subject or not body:
        flash("Subject and message are required.", "danger")
        return redirect(url_for("candidates"))

    ids = []
    for value in raw_ids:
        try:
            cid = int(value)
            if cid > 0 and cid not in ids:
                ids.append(cid)
        except (TypeError, ValueError):
            continue
    if not ids:
        flash("No valid candidates were selected.", "danger")
        return redirect(url_for("candidates"))

    con = db()
    placeholders = ",".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT id,name,email FROM candidates WHERE id IN ({placeholders}) ORDER BY id", ids
    ).fetchall()
    con.close()
    recipients = []
    skipped = []
    for row in rows:
        email = (row["email"] or "").strip()
        if email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            recipients.append(email)
        else:
            skipped.append(row["name"] or f"Candidate #{row['id']}")

    # De-duplicate addresses so a candidate is never sent the same BCC twice.
    recipients = list(dict.fromkeys(recipients))
    if not recipients:
        flash("None of the selected candidates have a valid email address.", "danger")
        return redirect(url_for("candidates"))

    ok, err = send_bulk_email_bcc(recipients, subject, body)
    if ok:
        audit("BULK_BCC_EMAIL", "CANDIDATE", None,
              f"{len(recipients)} recipients; skipped {len(skipped)} without valid email")
        message = f"Email sent to {len(recipients)} selected candidate(s) using BCC."
        if skipped:
            message += f" Skipped {len(skipped)} candidate(s) without a valid email address."
        flash(message, "success")
    else:
        flash(f"Bulk email failed: {err}", "danger")
    return redirect(url_for("candidates"))

@app.route("/candidates/<int:cid>/scan-pdf", methods=["POST"])
@permission_required("manage_candidates")
def scan_candidate_pdf(cid):
    """Upload/scan a candidate PDF resume directly from the profile and persist extracted data."""
    con=db(); c=con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not c:
        con.close(); flash("Candidate not found.", "danger"); return redirect(url_for("candidates"))
    f=request.files.get("resume_pdf")
    if not f or not f.filename:
        con.close(); flash("Select a PDF resume to scan.", "warning"); return redirect(url_for("candidate_detail", cid=cid))
    if Path(f.filename).suffix.lower() != ".pdf":
        con.close(); flash("Candidate profile scanner accepts PDF files only.", "danger"); return redirect(url_for("candidate_detail", cid=cid))
    filename=f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    path=config.UPLOAD_DIR/filename
    try:
        save_resume_upload(f, path)
        text=extract_text(path)
        if not text.strip():
            path.unlink(missing_ok=True); con.close()
            flash("The PDF could not be read. Please upload a text-readable PDF.", "danger")
            return redirect(url_for("candidate_detail", cid=cid))
        # Keep the scanned PDF as the candidate's current resume.
        old_file=c["resume_file"]
        con.execute("UPDATE candidates SET resume_file=?,resume_text=?,updated_at=? WHERE id=?",
                    (filename,text,datetime.now().isoformat(timespec="seconds"),cid))
        con.commit()
        if old_file and old_file != filename:
            try: (config.UPLOAD_DIR/old_file).unlink(missing_ok=True)
            except Exception: pass

        # Local-only PDF scanner: extract text and basic contact/profile fields without any AI/API call.
        lines=[ln.strip() for ln in text.splitlines() if ln.strip()]
        email_match=re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        phone_match=re.search(r"(?:\+?\d[\d .()\-]{7,}\d)", text)
        extracted_email=email_match.group(0) if email_match else c["email"]
        extracted_phone=phone_match.group(0).strip() if phone_match else c["phone"]
        extracted_name=c["name"]
        if lines:
            for ln in lines[:8]:
                if (3 <= len(ln) <= 80 and not re.search(r"@|https?://|\b(?:resume|curriculum vitae|cv|mobile|phone|email)\b", ln, re.I)
                    and not re.search(r"\d{3,}", ln) and len(ln.split()) <= 6):
                    extracted_name=ln
                    break
        con.execute("UPDATE candidates SET name=?,email=?,phone=?,resume_file=?,resume_text=?,updated_at=? WHERE id=?",
                    (extracted_name,extracted_email,extracted_phone,filename,text,datetime.now().isoformat(timespec="seconds"),cid))
        con.commit()
        audit("PDF_SCAN","CANDIDATE",cid,"PDF resume scanned locally without AI")
        flash("PDF scanned successfully. Resume text and basic contact details were extracted locally without AI.", "success")
    except ValueError as e:
        path.unlink(missing_ok=True); con.close(); flash(str(e), "danger")
        return redirect(url_for("candidate_detail", cid=cid))
    except Exception as e:
        path.unlink(missing_ok=True)
        con.close(); flash(f"PDF scan failed: {e}", "danger")
        return redirect(url_for("candidate_detail", cid=cid))
    con.close()
    return redirect(url_for("candidate_detail", cid=cid))

@app.route("/candidates/<int:cid>")
@staff_only
def candidate_detail(cid):
    con=db(); c=con.execute("SELECT * FROM candidates WHERE id=?",(cid,)).fetchone()
    apps=con.execute("""SELECT a.*,j.title job FROM applications a JOIN jobs j ON j.id=a.job_id
                        WHERE a.candidate_id=? ORDER BY a.id DESC""",(cid,)).fetchall()
    con.close()
    if not c: return "Candidate not found",404
    return render_template("candidate_detail.html",candidate=c,applications=apps)

@app.route("/candidate-interviewer-panel/<int:cid>")
@login_required
def candidate_interviewer_panel(cid):
    # The old combined Candidate & Interviewer panel is intentionally removed from Recruitment.
    # Candidate and Interviewer users now use their own role-specific portals.
    if current_user()["role"] in {"Candidate", "Interviewer"}:
        return redirect(url_for("dashboard"))
    return redirect(url_for("candidate_detail", cid=cid))

@app.route("/candidates/<int:cid>/edit", methods=["GET","POST"])
@permission_required("manage_candidates")
def edit_candidate(cid):
    con=db(); c=con.execute("SELECT * FROM candidates WHERE id=?",(cid,)).fetchone()
    if not c:
        con.close(); flash("Candidate not found.","danger"); return redirect(url_for("candidates"))
    if request.method=="POST":
        name=request.form["name"].strip(); email=request.form.get("email","").strip()
        filename=c["resume_file"]; text=c["resume_text"]
        f=request.files.get("resume")
        if f and f.filename:
            if not allowed_file(f.filename):
                con.close(); flash("Only PDF, DOCX and TXT resumes are allowed.","danger")
                return render_template("candidate_form.html",candidate=c)
            if filename:
                try: (config.UPLOAD_DIR/filename).unlink(missing_ok=True)
                except Exception: pass
            filename=f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"; p=config.UPLOAD_DIR/filename
            try:
                save_resume_upload(f, p)
            except ValueError as e:
                flash(str(e), "danger"); return render_template("candidate_form.html")
            text=extract_text(p)
        photo=c["photo"]
        photo_file=request.files.get("photo")
        if photo_file and photo_file.filename:
            if not allowed_image(photo_file.filename):
                con.close(); flash("Photo must be PNG, JPG, WEBP or GIF.","danger")
                return render_template("candidate_form.html",candidate=c)
            if photo:
                try: (config.UPLOAD_DIR/photo).unlink(missing_ok=True)
                except Exception: pass
            photo=f"{uuid.uuid4().hex}_candidate.jpg"; compress_candidate_photo(photo_file, photo)
        if request.form.get("remove_photo")=="1" and not (photo_file and photo_file.filename):
            if photo:
                try: (config.UPLOAD_DIR/photo).unlink(missing_ok=True)
                except Exception: pass
            photo=""
        now=datetime.now().isoformat(timespec="seconds")
        con.execute("""UPDATE candidates SET name=?,email=?,phone=?,location=?,resume_file=?,resume_text=?,
                       skills=?,experience_years=?,education=?,photo=?,updated_at=? WHERE id=?""",
                    (name,email,request.form.get("phone",""),request.form.get("location",""),filename,text,
                     request.form.get("skills",""),float(request.form.get("experience_years") or 0),
                     request.form.get("education",""),photo,now,cid))
        con.commit(); con.close()
        audit("UPDATE","CANDIDATE",cid,name); flash("Candidate updated.","success")
        return redirect(url_for("candidate_detail",cid=cid))
    con.close()
    return render_template("candidate_form.html",candidate=c)

@app.route("/candidates/<int:cid>/delete", methods=["POST"])
@permission_required("manage_candidates")
def delete_candidate(cid):
    con = db()
    c = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not c:
        con.close(); flash("Candidate not found.", "danger"); return redirect(url_for("candidates"))
    con.execute("DELETE FROM candidates WHERE id=?", (cid,))
    con.commit(); con.close()
    if c["resume_file"]:
        try: (config.UPLOAD_DIR / c["resume_file"]).unlink(missing_ok=True)
        except Exception: pass
    if c["photo"]:
        try: (config.UPLOAD_DIR / c["photo"]).unlink(missing_ok=True)
        except Exception: pass
    audit("DELETE", "CANDIDATE", cid, c["name"])
    flash("Candidate deleted.", "success")
    return redirect(url_for("candidates"))

@app.route("/applications")
@staff_only
def applications():
    con=db(); rows=con.execute("""SELECT a.*,c.name candidate,c.email,j.title job,
        (SELECT i.id FROM interviews i WHERE i.application_id=a.id AND i.round_name='AI Interview - Round 1' ORDER BY i.id DESC LIMIT 1) ai_interview_id,
        (SELECT i.status FROM interviews i WHERE i.application_id=a.id AND i.round_name='AI Interview - Round 1' ORDER BY i.id DESC LIMIT 1) ai_interview_status,
        (SELECT i.ai_report FROM interviews i WHERE i.application_id=a.id AND i.round_name='AI Interview - Round 1' ORDER BY i.id DESC LIMIT 1) ai_interview_report
        FROM applications a JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id ORDER BY a.id DESC""").fetchall()
    jobs=con.execute("SELECT id,title FROM jobs WHERE status='Open' ORDER BY title").fetchall()
    candidates=con.execute("SELECT id,name FROM candidates ORDER BY name").fetchall(); con.close()
    rows=[dict(r) for r in rows]
    for r in rows:
        try: d=json.loads(r.get("ai_screening") or "{}")
        except ValueError: d={}
        sug=d.get("suggested_status")
        r["ai_suggestion"]=sug if (d.get("ai_decided") is False or d.get("note")) and sug and sug!=r.get("status") else ""
        try: r["ai_report_data"] = json.loads(r.get("ai_interview_report") or "{}")
        except (TypeError, ValueError): r["ai_report_data"] = {}
    return render_template("applications.html",applications=rows,jobs=jobs,candidates=candidates)

@app.route("/applications/add",methods=["POST"])
@permission_required("manage_candidates")
def add_application():
    con=db(); now=datetime.now().isoformat(timespec="seconds")
    try:
        con.execute("INSERT INTO applications(candidate_id,job_id,status,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (request.form["candidate_id"],request.form["job_id"],"Applied",request.form.get("source","Website"),now,now))
        con.commit(); flash("Application created.","success")
    except sqlite3.IntegrityError: flash("This candidate is already applied to this job.","warning")
    con.close(); return redirect(url_for("dashboard") if request.form.get("next")=="dashboard" else url_for("applications"))

@app.route("/applications/<int:aid>/status",methods=["POST"])
@permission_required("manage_candidates")
def update_status(aid):
    allowed_statuses = {"Applied", "Screening", "Accepted", "Hold", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"}
    status = request.form.get("status", "Applied")
    if status not in allowed_statuses:
        flash("Invalid application status.", "danger")
        return redirect(url_for("applications"))
    con = db()
    application = con.execute("SELECT id FROM applications WHERE id=?", (aid,)).fetchone()
    if not application:
        con.close(); flash("Application not found.", "danger"); return redirect(url_for("applications"))
    con.execute("UPDATE applications SET status=?,updated_at=? WHERE id=?",
                (status, datetime.now().isoformat(timespec="seconds"), aid))
    invite_created = False
    if status in {"Accepted", "Shortlisted", "Interview"}:
        _, invite_created = ensure_ai_interview_invite(con, aid)
    con.commit(); con.close()
    audit("STATUS_CHANGE", "APPLICATION", aid, status)
    if invite_created:
        flash("Candidate selected. A 48-hour AI first-round interview invitation is now available in the candidate's account.", "success")
    return redirect(url_for("applications"))

@app.route("/applications/<int:aid>/delete", methods=["POST"])
@permission_required("manage_candidates")
def delete_application(aid):
    con = db()
    a = con.execute("SELECT * FROM applications WHERE id=?", (aid,)).fetchone()
    if not a:
        con.close(); flash("Application not found.", "danger"); return redirect(url_for("applications"))
    candidate_id = a["candidate_id"]
    con.execute("DELETE FROM applications WHERE id=?", (aid,))
    con.commit(); con.close()
    audit("DELETE", "APPLICATION", aid)
    flash("Application removed.", "success")
    if request.form.get("next") == "candidate":
        return redirect(url_for("candidate_detail", cid=candidate_id))
    return redirect(url_for("applications"))


@app.route("/candidates/<int:cid>/photo",methods=["POST"])
@permission_required("manage_candidates")
def candidate_photo(cid):
    """Upload, replace or remove a candidate's profile photo straight from the profile page."""
    con=db(); c=con.execute("SELECT id,photo FROM candidates WHERE id=?",(cid,)).fetchone()
    if not c:
        con.close(); flash("Candidate not found.","danger"); return redirect(url_for("candidates"))
    photo=c["photo"] or ""
    f=request.files.get("photo")
    if request.form.get("remove_photo")=="1" and not (f and f.filename):
        if photo:
            try: (config.UPLOAD_DIR/photo).unlink(missing_ok=True)
            except Exception: pass
        con.execute("UPDATE candidates SET photo='',updated_at=? WHERE id=?",(datetime.now().isoformat(timespec="seconds"),cid))
        con.commit(); con.close(); flash("Photo removed.","success"); return redirect(url_for("candidate_detail",cid=cid))
    if not f or not f.filename:
        con.close(); flash("Choose a photo first.","warning"); return redirect(url_for("candidate_detail",cid=cid))
    if not allowed_image(f.filename):
        con.close(); flash("Photo must be PNG, JPG, WEBP or GIF.","danger"); return redirect(url_for("candidate_detail",cid=cid))
    new=f"{uuid.uuid4().hex}_candidate.jpg"; compress_candidate_photo(f,new)
    if photo:
        try: (config.UPLOAD_DIR/photo).unlink(missing_ok=True)
        except Exception: pass
    con.execute("UPDATE candidates SET photo=?,updated_at=? WHERE id=?",(new,datetime.now().isoformat(timespec="seconds"),cid))
    con.commit(); con.close(); audit("UPDATE","CANDIDATE",cid,"photo updated"); flash("Photo saved.","success")
    return redirect(url_for("candidate_detail",cid=cid))
