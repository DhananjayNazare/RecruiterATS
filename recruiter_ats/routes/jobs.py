"""Positions (jobs): list, create, delete and attach a JD."""
import os
import threading
from datetime import datetime
from flask import flash, redirect, render_template, request, session, url_for
from recruiter_ats import config
from recruiter_ats.config import app
from recruiter_ats.database import db, row_get
from recruiter_ats.routes.ai import ai_find_job_matches
from recruiter_ats.security import audit, permission_required, staff_only
from recruiter_ats.services.ai import ai_configured, ai_threshold_default
from recruiter_ats.services.files import save_jd_upload


@app.route("/jobs")
@app.route("/positions")
@staff_only
def jobs():
    con=db(); rows=con.execute("""SELECT j.*,c.name company,l.name location,ca.name category,
        (SELECT COUNT(*) FROM applications a WHERE a.job_id=j.id) application_count
        FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN locations l ON l.id=j.location_id
        LEFT JOIN categories ca ON ca.id=j.category_id ORDER BY j.id DESC""").fetchall()
    positions=[]
    for j in rows:
        apps=con.execute("""SELECT a.id,a.status,a.match_score,c.id candidate_id,c.name candidate FROM applications a
                            JOIN candidates c ON c.id=a.candidate_id WHERE a.job_id=?
                            ORDER BY COALESCE(a.match_score,-1) DESC,a.id DESC LIMIT 8""",(j["id"],)).fetchall()
        positions.append({"id":j["id"],"title":j["title"],"company":j["company"] or "","location":j["location"] or "",
                          "category":j["category"] or "","employment_type":j["employment_type"] or "","status":j["status"] or "Open",
                          "description":j["description"] or "","requirements":j["requirements"] or "",
                          "jd_file":row_get(j,"jd_file"),"jd_original_name":row_get(j,"jd_original_name"),
                          "jd_updated_at":row_get(j,"jd_updated_at"),"application_count":j["application_count"],
                          "applications":[dict(x) for x in apps]})
    companies=con.execute("SELECT * FROM companies ORDER BY name").fetchall()
    categories=con.execute("SELECT * FROM categories ORDER BY name").fetchall()
    locations=con.execute("SELECT * FROM locations ORDER BY name").fetchall(); con.close()
    return render_template("jobs.html",jobs=rows,positions=positions,companies=companies,categories=categories,locations=locations)

@app.route("/jobs/add",methods=["POST"])
@permission_required("manage_jobs")
def add_job():
    now=datetime.now().isoformat(timespec="seconds")
    jd_stored=jd_original=jd_text=""
    jd_upload=request.files.get("jd_file")
    if jd_upload and jd_upload.filename:
        try:
            jd_stored,jd_original,jd_text=save_jd_upload(jd_upload)
        except ValueError as e:
            flash(str(e),"danger"); return redirect(url_for("jobs"))
    description=(request.form.get("description") or "").strip() or jd_text
    if not description:
        flash("Add a job description or attach a JD file.","danger"); return redirect(url_for("jobs"))
    con=db()
    category_id=None
    category_name=request.form.get("category_name","").strip()
    if category_name:
        row=con.execute("SELECT id FROM categories WHERE lower(name)=?", (category_name.lower(),)).fetchone()
        if row:
            category_id=row["id"]
        else:
            con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (category_name,))
            row=con.execute("SELECT id FROM categories WHERE lower(name)=?", (category_name.lower(),)).fetchone()
            category_id=row["id"] if row else None
    location_id=None
    location_name=request.form.get("location","").strip()
    if location_name:
        row=con.execute("SELECT id FROM locations WHERE lower(name)=?", (location_name.lower(),)).fetchone()
        if row:
            location_id=row["id"]
        else:
            con.execute("INSERT OR IGNORE INTO locations(name) VALUES(?)", (location_name,))
            row=con.execute("SELECT id FROM locations WHERE lower(name)=?", (location_name.lower(),)).fetchone()
            location_id=row["id"] if row else None
    con.execute("""INSERT INTO jobs(title,company_id,category_id,location_id,description,requirements,employment_type,status,created_by,created_at,updated_at,jd_file,jd_original_name,jd_text,jd_updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request.form["title"],request.form.get("company_id") or None,category_id,
                 location_id,description,request.form.get("requirements",""),
                 request.form.get("employment_type","Full-time"),"Open",session["user_id"],now,now,
                 jd_stored or None,jd_original or None,jd_text or None,now if jd_stored else None))
    jid=con.execute("SELECT last_insert_rowid()").fetchone()[0]; con.commit(); con.close(); audit("CREATE","JOB",jid)
    # New JDs automatically trigger AI matching against only the first 50 existing candidates.
    creator_id=session.get("user_id")
    if ai_configured() and creator_id:
        def _auto_match_new_jd(job_id, uid):
            try:
                with app.test_request_context("/ai/job/%s/find-matches" % job_id, method="POST", json={"threshold":ai_threshold_default(),"auto_move":True,"schedule":False}):
                    session["user_id"] = uid
                    ai_find_job_matches(job_id)
            except Exception as exc:
                app.logger.exception("Automatic JD candidate matching failed: %s", exc)
        threading.Thread(target=_auto_match_new_jd, args=(jid,creator_id), daemon=True).start()
    flash("Position created. AI matching against existing candidates has started.", "success")
    return redirect(url_for("jobs"))

@app.route("/jobs/<int:jid>/delete", methods=["POST"])
@permission_required("manage_jobs")
def delete_job(jid):
    con = db()
    job = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not job:
        con.close(); flash("Position not found.", "danger"); return redirect(url_for("jobs"))
    con.execute("DELETE FROM jobs WHERE id=?", (jid,))
    con.commit(); con.close()
    audit("DELETE", "JOB", jid, job["title"])
    if row_get(job, "jd_file"):
        try: (config.UPLOAD_DIR / job["jd_file"]).unlink(missing_ok=True)
        except Exception: pass
    flash("Position deleted.", "success")
    return redirect(url_for("jobs"))

@app.route("/positions/<int:jid>/jd", methods=["POST"])
@permission_required("manage_jobs")
def update_position_jd(jid):
    """Popup form on the Positions page: attach/replace the JD file and edit the JD text."""
    con=db(); job=con.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
    if not job:
        con.close(); flash("Position not found.","danger"); return redirect(url_for("jobs"))
    now=datetime.now().isoformat(timespec="seconds")
    title=(request.form.get("title") or "").strip() or job["title"]
    status=request.form.get("status") if request.form.get("status") in ("Open","Closed","On Hold") else (job["status"] or "Open")
    description=(request.form.get("description") or "").strip() or job["description"]
    requirements=(request.form.get("requirements") or "").strip()
    jd_file, jd_name, jd_text, jd_at = row_get(job,"jd_file"), row_get(job,"jd_original_name"), row_get(job,"jd_text"), row_get(job,"jd_updated_at")
    upload=request.files.get("jd_file")
    if upload and upload.filename:
        try:
            stored,original,text=save_jd_upload(upload)
        except ValueError as e:
            con.close(); flash(str(e),"danger"); return redirect(url_for("jobs",open=jid))
        old=jd_file
        jd_file,jd_name,jd_text,jd_at=stored,original,text,now
        if not (request.form.get("description") or "").strip():
            description=text
        if old:
            try: (config.UPLOAD_DIR/old).unlink(missing_ok=True)
            except Exception: pass
    con.execute("""UPDATE jobs SET title=?,status=?,description=?,requirements=?,jd_file=?,jd_original_name=?,jd_text=?,jd_updated_at=?,updated_at=? WHERE id=?""",
                (title,status,description,requirements,jd_file or None,jd_name or None,jd_text or None,jd_at or None,now,jid))
    con.commit(); con.close()
    audit("UPDATE","JOB",jid,"JD updated" + (" (file attached)" if upload and upload.filename else ""))
    flash("Position and JD saved. AI Screening will use this JD.","success")
    return redirect(url_for("jobs"))   # popup closes after a successful save
