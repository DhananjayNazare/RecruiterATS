"""User management, permissions, two-factor admin and scheduled emails."""
import sqlite3
from datetime import datetime
from flask import flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from recruiter_ats.config import PERMISSIONS, ROLES, app
from recruiter_ats.database import db
from recruiter_ats.security import admin_required, audit, current_user, has_permission, login_required, user_management_required


@app.route("/admin")
@user_management_required
def admin():
    con=db(); users=con.execute("SELECT id,name,email,role,active,created_at,totp_enabled,totp_required FROM users WHERE COALESCE(is_super,0)=0 ORDER BY id").fetchall()
    logs=con.execute("""SELECT l.*,u.name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id
                        WHERE COALESCE(u.is_super,0)=0
                        ORDER BY l.id DESC LIMIT 50""").fetchall()
    permissions={}
    for u in users:
        permissions[u["id"]]={}
        for key,_,_ in PERMISSIONS:
            row=con.execute("SELECT granted FROM user_permissions WHERE user_id=? AND permission=?",(u["id"],key)).fetchone()
            permissions[u["id"]][key]=bool(row and row["granted"])
    applications_list=con.execute("""SELECT a.id,c.name candidate_name,c.email candidate_email,j.title job_title
                                     FROM applications a JOIN candidates c ON c.id=a.candidate_id
                                     JOIN jobs j ON j.id=a.job_id ORDER BY a.id DESC""").fetchall()
    scheduled_emails=con.execute("SELECT * FROM email_schedules ORDER BY id DESC LIMIT 50").fetchall()
    candidates_for_users=con.execute("SELECT id,name,email FROM candidates ORDER BY name").fetchall()
    con.close()
    return render_template("admin.html",users=users,logs=logs,permissions=permissions,roles=ROLES,candidates_for_users=candidates_for_users,
                            permission_defs=PERMISSIONS,applications_list=applications_list,
                            scheduled_emails=scheduled_emails)

@app.route("/admin/users/add",methods=["POST"])
@admin_required
def add_user():
    con=db()
    try:
        role=request.form.get("role","Recruiter")
        if role not in ROLES: role="Recruiter"
        candidate_id=request.form.get("candidate_id") or None
        # New non-Admin users are automatically marked for mandatory 2FA enrollment.
        totp_required = 0 if role == "Admin" else 1
        con.execute("INSERT INTO users(name,email,password_hash,role,candidate_id,created_at,totp_required) VALUES(?,?,?,?,?,?,?)",
                    (request.form["name"],request.form["email"].lower().strip(),generate_password_hash(request.form["password"]),
                     role,candidate_id,datetime.now().isoformat(timespec="seconds"),totp_required))
        con.commit(); flash("User created.","success")
    except sqlite3.IntegrityError: flash("Email already exists.","danger")
    con.close(); return redirect(url_for("admin"))

@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@user_management_required
def edit_user(user_id):
    actor = current_user()
    con = db()
    target = con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0", (user_id,)).fetchone()
    candidates_for_users = con.execute("SELECT id,name,email FROM candidates ORDER BY name").fetchall()
    if not target:
        con.close(); flash("User not found.", "danger"); return redirect(url_for("admin"))
    if actor["role"] != "Admin" and target["role"] == "Admin":
        con.close(); flash("Managers cannot edit Admin accounts.", "danger"); return redirect(url_for("admin"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", target["role"])
        candidate_id = request.form.get("candidate_id") or None
        password = request.form.get("password", "")

        if not name or not email:
            con.close(); flash("Name and email are required.", "danger"); return redirect(url_for("edit_user", user_id=user_id))
        if role not in ROLES:
            role = target["role"]
        if actor["role"] != "Admin" and role == "Admin":
            con.close(); flash("Managers cannot assign the Admin role.", "danger"); return redirect(url_for("edit_user", user_id=user_id))
        if target["id"] == actor["id"] and role != target["role"]:
            con.close(); flash("You cannot change your own role.", "warning"); return redirect(url_for("edit_user", user_id=user_id))

        duplicate = con.execute("SELECT id FROM users WHERE lower(email)=? AND id!=?", (email, user_id)).fetchone()
        if duplicate:
            con.close(); flash("Another user already has this email address.", "danger"); return redirect(url_for("edit_user", user_id=user_id))

        if password and target["voice_pw_hash"] and check_password_hash(target["voice_pw_hash"], password):
            con.close(); flash("The login password must be different from this user's voice password.", "danger")
            return redirect(url_for("edit_user", user_id=user_id))
        if password:
            con.execute("UPDATE users SET name=?,email=?,role=?,candidate_id=?,password_hash=? WHERE id=?",
                        (name, email, role, candidate_id, generate_password_hash(password), user_id))
            # A manually changed password should require the user to use the new password only;
            # do not silently remove an existing mandatory 2FA requirement.
        else:
            con.execute("UPDATE users SET name=?,email=?,role=?,candidate_id=? WHERE id=?",
                        (name, email, role, candidate_id, user_id))
        con.commit(); con.close()
        audit("USER_UPDATED", "USER", user_id, f"Updated account details for {email}")
        flash(f"User {name} updated successfully.", "success")
        return redirect(url_for("admin"))

    con.close()
    return render_template("user_edit.html", target=target, roles=ROLES, candidates_for_users=candidates_for_users)

@app.route("/admin/users/permissions",methods=["POST"])
@admin_required
def set_user_permissions():
    user_id=int(request.form.get("user_id",0))
    con=db(); target=con.execute("SELECT id,name,role FROM users WHERE id=? AND COALESCE(is_super,0)=0",(user_id,)).fetchone()
    if not target:
        con.close(); flash("User not found.","danger"); return redirect(url_for("admin"))
    if target["role"]=="Admin":
        con.close(); flash("Admin accounts already have full access.","warning"); return redirect(url_for("admin"))
    for key,_,_ in PERMISSIONS:
        granted=1 if request.form.get(f"perm_{key}")=="1" else 0
        con.execute("""INSERT INTO user_permissions(user_id,permission,granted) VALUES(?,?,?)
                       ON CONFLICT(user_id,permission) DO UPDATE SET granted=excluded.granted""",(user_id,key,granted))
    con.commit(); con.close()
    audit("PERMISSIONS_UPDATED","USER",user_id,target["name"])
    flash(f"Permission level updated for {target['name']}.","success")
    return redirect(url_for("admin"))

@app.route("/admin/users/toggle",methods=["POST"])
@user_management_required
def toggle_user():
    user_id=int(request.form.get("user_id",0)); actor=current_user(); con=db(); target=con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0",(user_id,)).fetchone()
    if not target:
        con.close(); flash("User not found.","danger"); return redirect(url_for("admin"))
    if target["id"]==actor["id"]:
        con.close(); flash("You cannot deactivate your own account.","warning"); return redirect(url_for("admin"))
    if actor["role"]!="Admin" and target["role"]=="Admin":
        con.close(); flash("Managers cannot change Admin accounts.","danger"); return redirect(url_for("admin"))
    new_active=0 if target["active"] else 1; con.execute("UPDATE users SET active=? WHERE id=?",(new_active,user_id)); con.commit(); con.close()
    audit("USER_ACTIVATED" if new_active else "USER_DEACTIVATED","USER",user_id,target["email"]); flash("User status updated.","success"); return redirect(url_for("admin"))

@app.route("/admin/users/delete",methods=["POST"])
@user_management_required
def delete_user():
    user_id=int(request.form.get("user_id",0)); actor=current_user(); con=db(); target=con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0",(user_id,)).fetchone()
    if not target:
        con.close(); flash("User not found.","danger"); return redirect(url_for("admin"))
    if target["id"]==actor["id"]:
        con.close(); flash("You cannot delete your own account.","warning"); return redirect(url_for("admin"))
    if actor["role"]!="Admin" and target["role"]=="Admin":
        con.close(); flash("Managers cannot delete Admin accounts.","danger"); return redirect(url_for("admin"))
    if target["role"]=="Admin":
        admins=con.execute("SELECT COUNT(*) c FROM users WHERE role='Admin' AND active=1 AND COALESCE(is_super,0)=0").fetchone()["c"]
        if admins<=1:
            con.close(); flash("The last active Admin cannot be deleted.","danger"); return redirect(url_for("admin"))
    con.execute("UPDATE jobs SET created_by=NULL WHERE created_by=?",(user_id,)); con.execute("UPDATE todos SET assigned_to=NULL WHERE assigned_to=?",(user_id,)); con.execute("UPDATE audit_log SET user_id=NULL WHERE user_id=?",(user_id,)); con.execute("DELETE FROM user_permissions WHERE user_id=?",(user_id,)); con.execute("DELETE FROM users WHERE id=?",(user_id,)); con.commit(); con.close()
    audit("USER_DELETED","USER",None,target["email"]); flash("User deleted successfully.","success"); return redirect(url_for("admin"))

@app.route("/admin/users/2fa",methods=["POST"])
@admin_required
def admin_manage_2fa():
    """Only Admins may require, un-require, or disable/reset another user's 2FA."""
    user_id=int(request.form.get("user_id",0))
    action=request.form.get("action","")
    actor=current_user()
    con=db(); target=con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0",(user_id,)).fetchone()
    if not target:
        con.close(); flash("User not found.","danger"); return redirect(url_for("admin"))
    if target["id"]==actor["id"]:
        con.close(); flash("Manage your own two-factor authentication from Settings.","warning"); return redirect(url_for("admin"))
    if action=="require":
        con.execute("UPDATE users SET totp_required=1 WHERE id=?",(user_id,)); con.commit()
        audit("2FA_REQUIRED","USER",user_id,target["email"])
        flash(f"Two-factor authentication is now required for {target['name']}. They'll be asked to set it up at their next sign-in.","success")
    elif action=="unrequire":
        con.execute("UPDATE users SET totp_required=0 WHERE id=?",(user_id,)); con.commit()
        audit("2FA_UNREQUIRED","USER",user_id,target["email"])
        flash(f"Two-factor authentication is no longer required for {target['name']}.","success")
    elif action=="disable":
        con.execute("UPDATE users SET totp_secret=NULL,totp_enabled=0 WHERE id=?",(user_id,)); con.commit()
        audit("2FA_ADMIN_DISABLED","USER",user_id,target["email"])
        flash(f"Two-factor authentication has been disabled/reset for {target['name']}.","success")
    else:
        con.close(); flash("Unknown action.","danger"); return redirect(url_for("admin"))
    con.close()
    return redirect(url_for("admin"))

@app.route("/admin/emails/schedule", methods=["POST"])
@login_required
def schedule_status_email():
    u=current_user()
    if not (u["role"]=="Admin" or has_permission("send_emails")):
        flash("You don't have permission to schedule status emails.","danger"); return redirect(url_for("dashboard"))
    application_id=request.form.get("application_id") or None
    recipient_type=request.form.get("recipient_type","Candidate")
    recipient_name=request.form.get("recipient_name","").strip()
    recipient_email=request.form.get("recipient_email","").strip()
    subject=request.form.get("subject","").strip()
    message=request.form.get("message","").strip()
    scheduled_at=request.form.get("scheduled_at","").strip()
    if not recipient_email or not subject or not message or not scheduled_at:
        flash("Recipient email, subject, message and schedule time are all required.","danger"); return redirect(url_for("dashboard"))
    con=db(); now=datetime.now().isoformat(timespec="seconds")
    con.execute("""INSERT INTO email_schedules(application_id,recipient_type,recipient_name,recipient_email,subject,message,scheduled_at,status,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (application_id,recipient_type,recipient_name,recipient_email,subject,message,scheduled_at,"Pending",u["id"],now))
    con.commit(); con.close()
    audit("EMAIL_SCHEDULED","EMAIL_SCHEDULE",None,f"{recipient_type} <{recipient_email}> @ {scheduled_at}")
    flash("Status update email scheduled.","success")
    return redirect(url_for("dashboard"))

@app.route("/admin/emails/<int:eid>/cancel", methods=["POST"])
@login_required
def cancel_scheduled_email(eid):
    u=current_user()
    if not (u["role"]=="Admin" or has_permission("send_emails")):
        flash("You don't have permission to manage scheduled emails.","danger"); return redirect(url_for("dashboard"))
    con=db(); row=con.execute("SELECT * FROM email_schedules WHERE id=?",(eid,)).fetchone()
    if not row:
        con.close(); flash("Scheduled email not found.","danger"); return redirect(url_for("dashboard"))
    if row["status"]!="Pending":
        con.close(); flash("Only pending emails can be cancelled.","warning"); return redirect(url_for("dashboard"))
    con.execute("UPDATE email_schedules SET status='Cancelled' WHERE id=?",(eid,)); con.commit(); con.close()
    audit("EMAIL_CANCELLED","EMAIL_SCHEDULE",eid)
    flash("Scheduled email cancelled.","success")
    return redirect(url_for("dashboard"))


@app.route("/admin/users/<int:user_id>/voice-reset", methods=["POST"])
@user_management_required
def reset_voice_password(user_id):
    """Clear a user's voice-command password; they must create a new one (with their login password) before using it again."""
    actor = current_user()
    con = db(); target = con.execute("SELECT * FROM users WHERE id=? AND COALESCE(is_super,0)=0", (user_id,)).fetchone()
    if not target:
        con.close(); flash("User not found.", "danger"); return redirect(url_for("admin"))
    if actor["role"] != "Admin" and target["role"] == "Admin":
        con.close(); flash("Managers cannot change Admin accounts.", "danger"); return redirect(url_for("admin"))
    con.execute("UPDATE users SET voice_pw_hash=NULL, voice_pw_changed_at=NULL, voice_failed=0, voice_locked_until=NULL WHERE id=?", (user_id,))
    con.commit(); con.close()
    audit("VOICE_PASSWORD_RESET", "USER", user_id, f"Voice-command password cleared for {target['email']}")
    flash(f"Voice-command password cleared for {target['name']}. They must set a new one.", "success")
    return redirect(url_for("admin"))
