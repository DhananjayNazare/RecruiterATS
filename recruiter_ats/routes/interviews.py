"""Interview scheduling, calendar, meeting rooms and job-portal integrations."""
import sqlite3
from datetime import datetime
from flask import flash, redirect, render_template, request, url_for
from recruiter_ats.config import app
from recruiter_ats.database import db
from recruiter_ats.services.scheduling import clean_meeting_link, clean_provider
from recruiter_ats.security import audit, current_user, login_required, permission_required, staff_only


@app.route("/interviews")
@staff_only
def interviews():
    con=db(); rows=con.execute("""SELECT i.*,a.candidate_id,c.name candidate,j.title job FROM interviews i
      JOIN applications a ON a.id=i.application_id JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
      ORDER BY i.scheduled_at""").fetchall()
    # Table order: upcoming first (soonest on top), then past ones (most recent first), so the first 10 are the useful ones.
    now=datetime.now().strftime("%Y-%m-%dT%H:%M")
    rows=[r for r in rows if (r["scheduled_at"] or "")>=now]+[r for r in reversed(rows) if (r["scheduled_at"] or "")<now]
    apps=con.execute("""SELECT a.id,c.name candidate,j.title job FROM applications a
      JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id ORDER BY c.name""").fetchall()
    interviewers=con.execute("SELECT id,name,email FROM users WHERE role='Interviewer' AND active=1 ORDER BY name").fetchall()
    con.close()
    return render_template("interviews.html",interviews=rows,applications=apps,interviewers=interviewers)

@app.route("/calendar")
@staff_only
def calendar_view():
    return interviews()

@app.route("/interviews/add",methods=["POST"])
@permission_required("manage_interviews")
def add_interview():
    con=db()
    interviewer_user_id=request.form.get("interviewer_user_id") or None
    interviewer=request.form.get("interviewer","")
    if interviewer_user_id:
        iu=con.execute("SELECT id,name,email FROM users WHERE id=? AND role='Interviewer' AND active=1",(interviewer_user_id,)).fetchone()
        if iu: interviewer=iu["name"]
    meeting_provider=clean_provider(request.form.get("meeting_provider"))
    meeting_link=clean_meeting_link(request.form.get("meeting_link"))
    if meeting_link is None:
        con.close(); flash("Meeting link must be a valid https:// URL (Teams, Google Meet, Zoom or other).","danger")
        return redirect(url_for("dashboard") if request.form.get("next")=="dashboard" else url_for("interviews"))
    back=redirect(url_for("dashboard") if request.form.get("next")=="dashboard" else url_for("interviews"))
    app_id=request.form.get("application_id","").strip()
    when=request.form.get("scheduled_at","").strip().replace(" ","T")[:16]
    try: datetime.strptime(when,"%Y-%m-%dT%H:%M")
    except ValueError:
        con.close(); flash("Pick a valid interview date and time.","danger"); return back
    if not app_id.isdigit() or not con.execute("SELECT 1 FROM applications WHERE id=?",(app_id,)).fetchone():
        con.close(); flash("Choose an existing application for the interview.","danger"); return back
    con.execute("""INSERT INTO interviews(application_id,round_name,scheduled_at,mode,interviewer,interviewer_user_id,meeting_link,meeting_provider,status)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                          (app_id,request.form.get("round_name","Round 1"),
                           when,request.form.get("mode","Online"),interviewer,interviewer_user_id,meeting_link,meeting_provider,"Scheduled"))
    con.commit(); con.close(); audit("CREATE","INTERVIEW"); flash("Interview scheduled.","success")
    return redirect(url_for("dashboard") if request.form.get("next")=="dashboard" else url_for("interviews"))

@app.route("/interviews/<int:iid>/delete", methods=["POST"])
@permission_required("manage_interviews")
def delete_interview(iid):
    con = db()
    i = con.execute("SELECT * FROM interviews WHERE id=?", (iid,)).fetchone()
    if not i:
        con.close(); flash("Interview not found.", "danger"); return redirect(url_for("interviews"))
    con.execute("DELETE FROM interviews WHERE id=?", (iid,))
    con.commit(); con.close()
    audit("DELETE", "INTERVIEW", iid)
    flash("Interview deleted.", "success")
    return redirect(url_for("interviews"))

@app.route("/integrations")
@staff_only
def integrations():
    con=db()
    portals=con.execute("SELECT * FROM job_portals ORDER BY enabled DESC,name").fetchall()
    jobs=con.execute("SELECT id,title,status FROM jobs ORDER BY id DESC").fetchall()
    posts=con.execute("""SELECT p.*,j.title job_title,jp.name portal_name
                         FROM job_portal_posts p JOIN jobs j ON j.id=p.job_id
                         JOIN job_portals jp ON jp.id=p.portal_id
                         ORDER BY p.id DESC LIMIT 100""").fetchall()
    con.close()
    return render_template("integrations.html",portals=portals,jobs=jobs,posts=posts)

@app.route("/integrations/portals/add",methods=["POST"])
@permission_required("manage_jobs")
def add_job_portal():
    name=request.form.get("name","").strip()
    if not name:
        flash("Portal name is required.","danger"); return redirect(url_for("integrations"))
    con=db()
    try:
        con.execute("INSERT INTO job_portals(name,website,posting_url,enabled,notes,created_at) VALUES(?,?,?,?,?,?)",
                    (name,request.form.get("website","").strip(),request.form.get("posting_url","").strip(),
                     1 if request.form.get("enabled")=="1" else 0,request.form.get("notes","").strip(),
                     datetime.now().isoformat(timespec="seconds")))
        con.commit(); flash("Job portal partner added.","success")
    except sqlite3.IntegrityError:
        flash("That portal already exists.","warning")
    con.close()
    return redirect(url_for("integrations"))

@app.route("/integrations/portals/<int:pid>/delete",methods=["POST"])
@permission_required("manage_jobs")
def delete_job_portal(pid):
    con=db(); con.execute("DELETE FROM job_portals WHERE id=?",(pid,)); con.commit(); con.close()
    flash("Job portal partner removed.","success"); return redirect(url_for("integrations"))

@app.route("/integrations/portals/<int:pid>/toggle",methods=["POST"])
@permission_required("manage_jobs")
def toggle_job_portal(pid):
    con=db(); con.execute("UPDATE job_portals SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(pid,))
    con.commit(); con.close(); return redirect(url_for("integrations"))

@app.route("/integrations/jobs/post",methods=["POST"])
@permission_required("manage_jobs")
def create_job_portal_post():
    job_id=request.form.get("job_id")
    portal_id=request.form.get("portal_id")
    if not job_id or not portal_id:
        flash("Select both a job and a portal.","danger"); return redirect(url_for("integrations"))
    con=db()
    job=con.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
    portal=con.execute("SELECT * FROM job_portals WHERE id=? AND enabled=1",(portal_id,)).fetchone()
    if not job or not portal:
        con.close(); flash("Job or enabled portal not found.","danger"); return redirect(url_for("integrations"))
    now=datetime.now().isoformat(timespec="seconds")
    con.execute("""INSERT INTO job_portal_posts(job_id,portal_id,status,created_at,updated_at)
                   VALUES(?,?, 'Ready to Post', ?, ?)
                   ON CONFLICT(job_id,portal_id) DO UPDATE SET status='Ready to Post',updated_at=excluded.updated_at""",
                (job_id,portal_id,now,now))
    con.commit(); con.close()
    flash(f"{portal['name']} posting prepared. Open the portal from the list to publish it.", "success")
    return redirect(url_for("integrations"))

@app.route("/interviews/<int:iid>/meeting",methods=["POST"])
@permission_required("manage_interviews")
def update_meeting(iid):
    """Set or change the meeting provider (Teams / Google Meet / Zoom / Custom) and link of an interview."""
    provider=clean_provider(request.form.get("meeting_provider"))
    link=clean_meeting_link(request.form.get("meeting_link"))
    if link is None:
        flash("Meeting link must be a valid https:// URL (Teams, Google Meet, Zoom or other).","danger"); return redirect(url_for("interviews"))
    con=db()
    cur=con.execute("UPDATE interviews SET meeting_provider=?,meeting_link=? WHERE id=?",(provider,link,iid))
    con.commit(); con.close()
    if not cur.rowcount: flash("Interview not found.","danger"); return redirect(url_for("interviews"))
    audit("UPDATE","INTERVIEW",iid,f"meeting: {provider}")
    flash("Meeting link saved." if link else "Meeting link removed.","success")
    return redirect(url_for("interviews"))

@app.route("/meeting/<int:iid>")
@login_required
def meeting_room(iid):
    token=request.args.get("token","")
    u=current_user()
    con=db()
    row=con.execute("""SELECT i.*,c.id candidate_id,c.name candidate,c.email candidate_email,j.title job,j.description job_description,j.requirements,
                              co.name company
                       FROM interviews i JOIN applications a ON a.id=i.application_id
                       JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
                       LEFT JOIN companies co ON co.id=j.company_id
                       WHERE i.id=?""",(iid,)).fetchone()
    if not row:
        con.close(); return "Meeting not found",404
    allowed=False
    if u["role"] in {"Admin","Recruiter","Manager"}:
        allowed=True
    elif u["role"]=="Interviewer":
        allowed = (row["interviewer_user_id"]==u["id"] or
                   (row["interviewer_user_id"] is None and
                    (row["interviewer"] or "").strip().lower() in {u["name"].strip().lower(),u["email"].strip().lower()}))
    elif u["role"]=="Candidate":
        allowed = (row["candidate_id"]==u["candidate_id"] or
                   (not u["candidate_id"] and (row["candidate_email"] or "").strip().lower()==u["email"].strip().lower()))
    if row["meeting_token"] and token and token != row["meeting_token"]:
        allowed=False
    if not allowed:
        con.close(); return "Meeting access denied",403
    con.close()
    return render_template("meeting_room.html",interview=row,participant_role=u["role"])


@app.route("/interviews/<int:iid>/edit",methods=["POST"])
@permission_required("manage_interviews")
def edit_interview(iid):
    """Change an interview's date/time, interviewer, round or mode (works for auto-scheduled interviews too)."""
    con=db(); iv=con.execute("SELECT * FROM interviews WHERE id=?",(iid,)).fetchone()
    if not iv:
        con.close(); flash("Interview not found.","danger"); return redirect(url_for("interviews"))
    when=(request.form.get("scheduled_at") or "").strip()
    try: datetime.strptime(when,"%Y-%m-%dT%H:%M")
    except ValueError:
        con.close(); flash("Enter a valid interview date and time.","danger"); return redirect(url_for("interviews"))
    name=(request.form.get("interviewer") or "").strip()
    uid=None
    if name:
        u=con.execute("SELECT id,name FROM users WHERE role='Interviewer' AND active=1 AND lower(name)=lower(?)",(name,)).fetchone()
        if u: uid,name=u["id"],u["name"]
    mode=request.form.get("mode") if request.form.get("mode") in ("Online","In-person","Phone") else (iv["mode"] or "Online")
    status=request.form.get("status") if request.form.get("status") in ("Scheduled","Completed","Cancelled") else (iv["status"] or "Scheduled")
    round_name=(request.form.get("round_name") or "").strip() or iv["round_name"]
    clash=con.execute("SELECT 1 FROM interviews WHERE id<>? AND scheduled_at=? AND status='Scheduled' AND lower(COALESCE(interviewer,''))=lower(?) AND ?<>''",(iid,when,name,name)).fetchone()
    con.execute("UPDATE interviews SET round_name=?,scheduled_at=?,mode=?,interviewer=?,interviewer_user_id=?,status=? WHERE id=?",
                (round_name,when,mode,name,uid,status,iid))
    con.commit(); con.close()
    audit("UPDATE","INTERVIEW",iid,f"rescheduled to {when}; interviewer: {name or '-'}")
    flash("Interview updated." + (" Note: this interviewer already has another interview at the same time." if clash else ""),"warning" if clash else "success")
    return redirect(url_for("interviews"))
