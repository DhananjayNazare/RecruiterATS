"""Dashboard, global search, notifications, reports and shared template context."""
import os
from datetime import datetime, timedelta
from flask import jsonify, redirect, render_template, request, url_for
from recruiter_ats.config import app
from recruiter_ats.database import db, get_setting
from recruiter_ats.security import current_user, has_permission, login_required, permission_required, staff_only
from recruiter_ats.services.scheduling import MEETING_PROVIDERS
from recruiter_ats.services.ai import ai_configured, ai_threshold_default, check_ai_connection


@app.route("/api/ai-status")
@staff_only
def api_ai_status():
    result=check_ai_connection(force=bool(request.args.get("force")))
    return jsonify(ok=True,**result)

@app.context_processor
def inject():
    u = current_user()
    can_manage = bool(u) and (u["role"] == "Admin" or has_permission("manage_users"))
    can_manage_candidates = bool(u) and (u["role"] == "Admin" or has_permission("manage_candidates"))
    return {"user": u, "ai_enabled": ai_configured(), "can_manage_users": can_manage, "can_manage_candidates": can_manage_candidates, "can_manage_jobs": bool(u) and (u["role"] == "Admin" or has_permission("manage_jobs")), "can_manage_interviews": bool(u) and (u["role"] == "Admin" or has_permission("manage_interviews")), "ai_switch": {k: get_setting(k, "1") == "1" for k in ("ai_master","ai_decide_status","ai_auto_schedule")}, "can_send_emails": bool(u) and (u["role"] == "Admin" or has_permission("send_emails")),
            "is_super": bool(u and u["is_super"]), "meeting_providers": MEETING_PROVIDERS,
            "is_candidate_portal": bool(u and u["role"] == "Candidate"),
            "is_interviewer_portal": bool(u and u["role"] == "Interviewer"),
            "is_meeting_room": bool(request.endpoint == "meeting_room")}

def _month_trend(con, table, col, where=""):
    """Real this-month vs last-month change for one KPI card (calendar months, counted by `col`)."""
    today = datetime.now().date()
    this_start = today.replace(day=1)
    next_start = (this_start + timedelta(days=32)).replace(day=1)
    last_start = (this_start - timedelta(days=1)).replace(day=1)
    sql = (f"SELECT COUNT(*) c FROM {table} WHERE {col} IS NOT NULL AND substr({col},1,10)>=? AND substr({col},1,10)<?"
           + (f" AND {where}" if where else ""))
    this_n = con.execute(sql, (this_start.isoformat(), next_start.isoformat())).fetchone()["c"]
    last_n = con.execute(sql, (last_start.isoformat(), this_start.isoformat())).fetchone()["c"]
    if last_n:
        pct = round((this_n - last_n) / last_n * 100, 1)
        direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
        label = "No change" if direction == "flat" else f"{abs(pct):g}%"
    elif this_n:
        direction, label = "up", f"{this_n} new"      # nothing last month, so a percentage is undefined
    else:
        direction, label = "flat", "No change"
    return {"direction": direction, "label": label,
            "tip": f"This month: {this_n} \u00b7 Last month: {last_n}"}


@app.route("/dashboard")
@login_required
def dashboard():
    u=current_user()
    if u["role"] == "Candidate":
        return redirect(url_for("candidate_panel"))
    if u["role"] == "Interviewer":
        return redirect(url_for("interviewer_panel"))
    con=db()
    stats={}
    for key,table in [("jobs","jobs"),("candidates","candidates"),("applications","applications"),("interviews","interviews")]:
        stats[key]=con.execute(f"SELECT COUNT(*) c FROM {table}" + (" WHERE status='Open'" if key=="jobs" else "")).fetchone()["c"]
    trends={
        "candidates":_month_trend(con,"candidates","created_at"),
        "applications":_month_trend(con,"applications","created_at"),
        "interviews":_month_trend(con,"interviews","scheduled_at"),   # interviews store no created date; scheduled date is used
        "jobs":_month_trend(con,"jobs","created_at"),
    }
    funnel=con.execute("SELECT status,COUNT(*) c FROM applications GROUP BY status ORDER BY c DESC").fetchall()
    recent=con.execute("""SELECT a.*,c.name candidate,j.title job FROM applications a
                          JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id
                          ORDER BY a.created_at DESC LIMIT 8""").fetchall()
    upcoming=con.execute("""SELECT i.*,c.name candidate,j.title job
                           FROM interviews i
                           JOIN applications a ON a.id=i.application_id
                           JOIN candidates c ON c.id=a.candidate_id
                           JOIN jobs j ON j.id=a.job_id
                           WHERE COALESCE(i.status,'Scheduled') NOT IN ('Cancelled','Completed')
                             AND i.scheduled_at IS NOT NULL AND i.scheduled_at >= ?
                           ORDER BY i.scheduled_at ASC LIMIT 5""",
                          (datetime.now().isoformat(timespec="minutes"),)).fetchall()
    todos=con.execute("SELECT * FROM todos ORDER BY status,due_date").fetchall()
    applications_list=con.execute("""SELECT a.id,c.name candidate_name,c.email candidate_email,j.title job_title
                                     FROM applications a JOIN candidates c ON c.id=a.candidate_id
                                     JOIN jobs j ON j.id=a.job_id ORDER BY a.id DESC""").fetchall()
    scheduled_emails=con.execute("SELECT * FROM email_schedules ORDER BY id DESC LIMIT 50").fetchall()
    positions=con.execute("SELECT id,title FROM jobs ORDER BY title").fetchall()
    companies=con.execute("SELECT * FROM companies ORDER BY name").fetchall()
    categories=con.execute("SELECT * FROM categories ORDER BY name").fetchall()
    locations=con.execute("SELECT * FROM locations ORDER BY name").fetchall()
    interview_apps=con.execute("""SELECT a.id,c.name candidate,j.title job FROM applications a
      JOIN candidates c ON c.id=a.candidate_id JOIN jobs j ON j.id=a.job_id ORDER BY c.name""").fetchall()
    interviewers=con.execute("SELECT id,name,email FROM users WHERE role='Interviewer' AND active=1 ORDER BY name").fetchall()
    app_candidates=con.execute("SELECT id,name FROM candidates ORDER BY name").fetchall()
    open_jobs=con.execute("SELECT id,title FROM jobs WHERE status='Open' ORDER BY title").fetchall()
    con.close()
    try: selected_position=int(request.args.get("position") or 0)
    except ValueError: selected_position=0
    return render_template("dashboard.html",stats=stats,trends=trends,funnel=funnel,recent=recent,upcoming=upcoming,todos=todos,
                           applications_list=applications_list,scheduled_emails=scheduled_emails,positions=positions,
                           companies=companies,categories=categories,locations=locations,applications=interview_apps,interviewers=interviewers,app_candidates=app_candidates,open_jobs=open_jobs,
                           can_interviews=bool(u["role"]=="Admin" or has_permission("manage_interviews")),
                           can_apps=bool(u["role"]=="Admin" or has_permission("manage_candidates")),
                           selected_position=selected_position,batch_threshold=ai_threshold_default(),
                           open_modal=(request.args.get("open") or ""))

@app.route("/api/global-search")
@staff_only
def global_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(ok=True, results=[])
    like = f"%{q}%"
    con = db()
    results = []
    for r in con.execute(
        "SELECT id,name,email,location FROM candidates "
        "WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? OR location LIKE ? OR skills LIKE ? "
        "ORDER BY name LIMIT 8", (like,like,like,like,like)
    ).fetchall():
        results.append({"type":"Candidate","id":r["id"],"title":r["name"],
                        "meta":r["email"] or r["location"] or "",
                        "url":url_for("candidate_detail", cid=r["id"])})
    seen=set()
    for r in con.execute(
        "SELECT DISTINCT c.id,c.name,j.title FROM candidates c JOIN applications a ON a.candidate_id=c.id "
        "JOIN jobs j ON j.id=a.job_id WHERE j.title LIKE ? ORDER BY c.name LIMIT 8", (like,)
    ).fetchall():
        seen.add(r["id"])
        results.append({"type":"Candidate","id":r["id"],"title":r["name"],"meta":"Position: "+r["title"],
                        "url":url_for("candidate_detail", cid=r["id"])})
    for r in con.execute(
        "SELECT id,title,status,employment_type FROM jobs "
        "WHERE title LIKE ? OR description LIKE ? OR requirements LIKE ? OR jd_text LIKE ? "
        "ORDER BY id DESC LIMIT 8", (like,like,like,like)
    ).fetchall():
        results.append({"type":"Position","id":r["id"],"title":r["title"],
                        "meta":r["status"] or "",
                        "url":url_for("jobs", open=r["id"])})
    for r in con.execute(
        "SELECT id,name,industry,website FROM companies "
        "WHERE name LIKE ? OR industry LIKE ? OR contact_person LIKE ? "
        "ORDER BY name LIMIT 8", (like,like,like)
    ).fetchall():
        results.append({"type":"Company","id":r["id"],"title":r["name"],
                        "meta":r["industry"] or "",
                        "url":url_for("operations") + "#company-" + str(r["id"])})
    con.close()
    return jsonify(ok=True, results=results[:20])

def _ensure_notif_table(con):
    con.execute("CREATE TABLE IF NOT EXISTS notification_clears(user_id INTEGER PRIMARY KEY, last_id INTEGER NOT NULL DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS notification_hidden(user_id INTEGER, audit_id INTEGER, PRIMARY KEY(user_id,audit_id))")

def _cleared_upto(con, uid):
    _ensure_notif_table(con)
    r=con.execute("SELECT last_id FROM notification_clears WHERE user_id=?", (uid,)).fetchone()
    return int(r["last_id"]) if r else 0

_NOTIF_BASE=("FROM audit_log WHERE COALESCE(user_id,0) NOT IN (SELECT id FROM users WHERE is_super=1) ")

@app.route("/api/notifications")
@staff_only
def notifications():
    con=db()
    uid=current_user()["id"]
    cleared=_cleared_upto(con, uid)
    rows=con.execute(
        "SELECT id,action,entity,details,created_at " + _NOTIF_BASE + "AND id>? AND id NOT IN (SELECT audit_id FROM notification_hidden WHERE user_id=?) ORDER BY id DESC LIMIT 8",
        (cleared, uid)
    ).fetchall()
    items=[]
    for r in rows:
        items.append({
            "id": r["id"],
            "title": r["action"].replace("_"," ").title(),
            "message": (r["details"] or f"{r['entity'] or 'System'} activity")[:120],
            "time": r["created_at"] or "",
        })
    con.close()
    return jsonify(ok=True, count=len(items), items=items)

@app.route("/api/notifications/clear", methods=["POST"])
@staff_only
def notifications_clear():
    """Clear all notifications for this user (or just one, when an id is given). Cleared items stay cleared."""
    con=db()
    uid=current_user()["id"]
    _ensure_notif_table(con)
    data=request.get_json(silent=True) or {}
    if data.get("id"):
        # dismiss just this one item
        con.execute("INSERT OR IGNORE INTO notification_hidden(user_id,audit_id) VALUES(?,?)", (uid, int(data["id"])))
    else:
        top=con.execute("SELECT COALESCE(MAX(id),0) m FROM audit_log").fetchone()["m"]
        con.execute("INSERT INTO notification_clears(user_id,last_id) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_id=excluded.last_id", (uid, top))
    con.commit(); con.close()
    return jsonify(ok=True)

@app.route("/reports")
@permission_required("view_reports")
def reports():
    con=db()
    by_status=[dict(r) for r in con.execute("SELECT COALESCE(NULLIF(status,''),'Unknown') status,COUNT(*) count FROM applications GROUP BY status ORDER BY count DESC").fetchall()]
    by_source=[dict(r) for r in con.execute("SELECT COALESCE(NULLIF(source,''),'Unknown') source,COUNT(*) count FROM applications GROUP BY source ORDER BY count DESC").fetchall()]
    by_job=[dict(r) for r in con.execute("""SELECT j.title,COUNT(a.id) applications,ROUND(AVG(a.match_score),1) avg_score
                          FROM jobs j LEFT JOIN applications a ON a.job_id=j.id
                          GROUP BY j.id ORDER BY applications DESC, j.title""").fetchall()]
    total=con.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"]
    hired=con.execute("SELECT COUNT(*) c FROM applications WHERE status='Hired'").fetchone()["c"]
    shortlisted=con.execute("SELECT COUNT(*) c FROM applications WHERE status='Shortlisted'").fetchone()["c"]
    interviews=con.execute("SELECT COUNT(*) c FROM applications WHERE status='Interview'").fetchone()["c"]
    ai_scored=con.execute("SELECT COUNT(*) c FROM applications WHERE match_score IS NOT NULL").fetchone()["c"]
    con.close()
    def pct(n): return round((n/total)*100,1) if total else 0
    metrics={"total":total,"hired":hired,"shortlisted":shortlisted,"interviews":interviews,"ai_scored":ai_scored,
             "hire_rate":pct(hired),"shortlist_rate":pct(shortlisted),"interview_rate":pct(interviews),"ai_coverage":pct(ai_scored)}
    return render_template("reports.html",by_status=by_status,by_source=by_source,by_job=by_job,metrics=metrics)
