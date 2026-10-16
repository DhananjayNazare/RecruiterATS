"""Settings page, personal 2FA, SMTP, backups and file serving."""
import os
import re
from flask import Response, flash, redirect, render_template, request, send_from_directory, session, url_for
from io import BytesIO
from pathlib import Path
from werkzeug.security import check_password_hash
from recruiter_ats import config
from recruiter_ats.config import app, pyotp, qrcode
from recruiter_ats.database import db, get_setting, set_setting
from recruiter_ats.security import admin_required, audit, current_user, login_required, super_required
from recruiter_ats.services.ai import ai_base_url, ai_configured, ai_model, ai_threshold_default, read_threshold
from recruiter_ats.services.backup import create_backup, prune_backups
from recruiter_ats.services.mailer import get_smtp_config, send_email
from recruiter_ats.services.scheduling import sched_config


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    u = current_user()
    is_admin_user = u["role"] == "Admin"
    if request.method == "POST":
        form_type = request.form.get("form_type", "backup")
        if form_type == "smtp":
            if not u["is_super"]:
                flash("You don't have access to that setting.", "danger")
                return redirect(url_for("settings"))
            set_setting("smtp_host", request.form.get("smtp_host", "").strip())
            set_setting("smtp_port", request.form.get("smtp_port", "587").strip() or "587")
            set_setting("smtp_username", request.form.get("smtp_username", "").strip())
            new_password = request.form.get("smtp_password", "")
            if new_password:
                set_setting("smtp_password", new_password)
            set_setting("smtp_use_tls", "1" if request.form.get("smtp_use_tls") == "1" else "0")
            set_setting("smtp_from_email", request.form.get("smtp_from_email", "").strip())
            set_setting("smtp_from_name", request.form.get("smtp_from_name", "Recruiter ARS").strip())
            audit("UPDATE", "SMTP_SETTINGS", details="SMTP server settings updated")
            flash("SMTP server settings saved.", "success")
            return redirect(url_for("settings"))
        if form_type == "ai_auto":
            if u["role"] != "Admin":
                flash("Administrator access required to change AI auto-screening settings.", "danger")
                return redirect(url_for("settings"))
            set_setting("ai_auto_threshold", read_threshold(request.form.get("ai_auto_threshold")))
            set_setting("sched_enabled", "1" if request.form.get("sched_enabled") == "1" else "0")
            days = sorted({int(x) for x in request.form.getlist("sched_days") if x.isdigit() and 0 <= int(x) <= 6}) or [0, 1, 2, 3, 4]
            set_setting("sched_work_days", ",".join(map(str, days)))
            for key, default in (("sched_start", "10:00"), ("sched_end", "18:00")):
                v = request.form.get(key, default).strip()
                set_setting(key, v if re.fullmatch(r"\d{1,2}:\d{2}", v) else default)
            for key, default, lo, hi in (("sched_duration", 45, 15, 240), ("sched_buffer", 15, 0, 120),
                                         ("sched_lead_hours", 4, 0, 168), ("sched_horizon_days", 14, 1, 60)):
                try: v = max(lo, min(hi, int(float(request.form.get(key, default)))))
                except (TypeError, ValueError): v = default
                set_setting(key, v)
            from recruiter_ats.services.scheduling import clean_meeting_link, clean_provider
            set_setting("sched_provider", clean_provider(request.form.get("sched_provider")))
            default_link = clean_meeting_link(request.form.get("sched_meeting_link"))
            if default_link is None:
                flash("Default meeting link must be a valid https:// URL; it was not saved.", "danger"); default_link = get_setting("sched_meeting_link", "")
            set_setting("sched_meeting_link", default_link)
            set_setting("sched_round", request.form.get("sched_round", "Round 1").strip() or "Round 1")
            set_setting("sched_notify_interviewer", "1" if request.form.get("sched_notify_interviewer") == "1" else "0")
            set_setting("public_base_url", request.form.get("public_base_url", "").strip().rstrip("/"))
            audit("UPDATE", "AI_AUTO_SETTINGS", details="AI auto-screening / interview scheduling settings updated")
            flash("AI auto-screening and interview scheduling settings saved.", "success")
            return redirect(url_for("settings"))
        if not is_admin_user:
            flash("Administrator access required to change backup settings.", "danger")
            return redirect(url_for("settings"))
        enabled = request.form.get("backup_enabled") == "1"
        set_setting("backup_enabled", "1" if enabled else "0")
        set_setting("backup_frequency", request.form.get("backup_frequency", "Daily"))
        set_setting("backup_time", request.form.get("backup_time", "02:00"))
        set_setting("backup_retention", request.form.get("backup_retention", "30"))
        custom_dir = request.form.get("backup_directory", "").strip()
        # Keep backups inside the application by default; custom paths are allowed for mounted/local backup storage.
        if custom_dir:
            config.BACKUP_DIR = Path(custom_dir).expanduser()
            config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            set_setting("backup_directory", str(config.BACKUP_DIR))
        else:
            set_setting("backup_directory", str(config.BACKUP_DIR))
        flash("Backup settings saved.", "success")
        return redirect(url_for("settings"))
    is_admin = is_admin_user
    smtp_cfg = get_smtp_config() if u["is_super"] else {"host":"", "port":"587", "username":"", "password":"", "use_tls":True, "from_email":"", "from_name":"Recruiter ARS"}
    sc = sched_config()
    ai_auto = {"threshold": ai_threshold_default(), "enabled": sc["enabled"], "days": sc["days"],
               "start": f"{sc['start']//60:02d}:{sc['start']%60:02d}", "end": f"{sc['end']//60:02d}:{sc['end']%60:02d}",
               "duration": sc["duration"], "buffer": sc["buffer"], "lead_hours": sc["lead_hours"], "horizon": sc["horizon"],
               "provider": sc["provider"], "meeting_link": sc["meeting_link"], "round": sc["round"], "notify_interviewer": sc["notify_interviewer"],
               "base_url": get_setting("public_base_url", "")}
    return render_template("settings.html",model=ai_model(),key_set=ai_configured(),base_url=ai_base_url(),
        is_admin=is_admin, show_smtp=bool(u["is_super"]), ai_auto=ai_auto,
        backup_enabled=get_setting("backup_enabled","0")=="1", backup_frequency=get_setting("backup_frequency","Daily"),
        backup_time=get_setting("backup_time","02:00"), backup_retention=get_setting("backup_retention","30"),
        backup_directory=get_setting("backup_directory",str(config.BACKUP_DIR)), backup_last=get_setting("backup_last",""),
        backup_error=get_setting("backup_last_error",""),
        smtp_host=smtp_cfg["host"], smtp_port=smtp_cfg["port"], smtp_username=smtp_cfg["username"],
        smtp_password_set=bool(smtp_cfg["password"]), smtp_use_tls=smtp_cfg["use_tls"],
        smtp_from_email=smtp_cfg["from_email"], smtp_from_name=smtp_cfg["from_name"],
        totp_enabled=bool(u["totp_enabled"]), totp_required=bool(u["totp_required"]), totp_available=bool(pyotp and qrcode))

@app.route("/settings/2fa/setup", methods=["POST"])
@login_required
def twofa_setup():
    """Generate a pending TOTP secret for the current user and show the QR/confirm step."""
    u=current_user()
    if not (pyotp and qrcode):
        flash("Two-factor authentication libraries are not installed on this server.","danger")
        return redirect(url_for("settings"))
    if u["totp_enabled"]:
        flash("Two-factor authentication is already enabled.","warning")
        return redirect(url_for("settings"))
    session["pending_totp_secret"]=pyotp.random_base32()
    return redirect(url_for("settings")+"#twofa")

@app.route("/settings/2fa/confirm", methods=["POST"])
@login_required
def twofa_confirm():
    u=current_user()
    secret=session.get("pending_totp_secret")
    if not secret or not pyotp:
        flash("Start two-factor setup again.","danger"); return redirect(url_for("settings"))
    code=re.sub(r"\D","",request.form.get("code",""))
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        flash("Incorrect code — please scan the QR code again and try the new 6-digit code.","danger")
        return redirect(url_for("settings")+"#twofa")
    con=db(); con.execute("UPDATE users SET totp_secret=?,totp_enabled=1 WHERE id=?",(secret,u["id"])); con.commit(); con.close()
    session.pop("pending_totp_secret",None)
    audit("2FA_ENABLED","USER",u["id"])
    flash("Two-factor authentication is now enabled on your account.","success")
    return redirect(url_for("settings")+"#twofa")

@app.route("/settings/2fa/cancel", methods=["POST"])
@login_required
def twofa_cancel_setup():
    session.pop("pending_totp_secret",None)
    return redirect(url_for("settings")+"#twofa")

@app.route("/settings/2fa/disable", methods=["POST"])
@login_required
def twofa_disable():
    u=current_user()
    if u["totp_required"]:
        flash("Your administrator requires two-factor authentication on this account. Contact an administrator to disable it.","danger")
        return redirect(url_for("settings")+"#twofa")
    if not check_password_hash(u["password_hash"], request.form.get("password","")):
        flash("Incorrect password — two-factor authentication was not changed.","danger")
        return redirect(url_for("settings")+"#twofa")
    con=db(); con.execute("UPDATE users SET totp_secret=NULL,totp_enabled=0 WHERE id=?",(u["id"],)); con.commit(); con.close()
    audit("2FA_DISABLED","USER",u["id"])
    flash("Two-factor authentication has been disabled.","success")
    return redirect(url_for("settings")+"#twofa")

@app.route("/settings/2fa/qrcode.png")
@login_required
def twofa_qrcode():
    u=current_user()
    secret=session.get("pending_totp_secret")
    if not secret or not qrcode: return "Not found",404
    uri=pyotp.totp.TOTP(secret).provisioning_uri(name=u["email"], issuer_name="Recruiter ARS")
    img=qrcode.make(uri)
    buf=BytesIO(); img.save(buf,format="PNG"); buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/settings/smtp/test", methods=["POST"])
@super_required
def smtp_test():
    u = current_user()
    ok, err = send_email(u["email"], "Recruiter ARS — Test Email",
                          "This is a test email from your Recruiter ARS SMTP settings. If you received this, your SMTP server is configured correctly.")
    if ok:
        audit("TEST", "SMTP_SETTINGS", details=f"Test email sent to {u['email']}")
        flash(f"Test email sent to {u['email']}.", "success")
    else:
        flash(f"Test email failed: {err}", "danger")
    return redirect(url_for("settings"))

@app.route("/settings/backup-now", methods=["POST"])
@admin_required
def backup_now():
    path, err = create_backup()
    if err:
        flash(f"Backup failed: {err}", "danger")
    else:
        prune_backups(); set_setting("backup_last_error", "")
        flash(f"Backup created successfully: {path.name}", "success")
    return redirect(url_for("settings"))

@app.route("/settings/demo-data", methods=["POST"])
@admin_required
def demo_data():
    """Load (or remove) the demo candidates. Demo rows use @demo.example e-mails; real data is never touched."""
    import contextlib, io, seed_demo_data
    try: count = max(1, min(5000, int(request.form.get("count") or 1000)))
    except ValueError: count = 1000
    with contextlib.redirect_stdout(io.StringIO()):
        if request.form.get("action") == "remove": seed_demo_data.main(reset_only=True)
        else: seed_demo_data.main(count)
    removed = request.form.get("action") == "remove"
    audit("DEMO_DATA", "SYSTEM", None, "removed" if removed else f"loaded {count}")
    flash("Demo data removed." if removed else f"{count} demo candidates loaded (earlier demo data was replaced).", "success")
    return redirect(url_for("settings"))

@app.route("/settings/backup-download/<path:name>")
@admin_required
def backup_download(name):
    safe = Path(name).name
    if not safe.startswith("recruiter_ars_backup_") or not safe.endswith(".zip"):
        return "Bad request", 400
    return send_from_directory(config.BACKUP_DIR, safe, as_attachment=True)

@app.route("/uploads/<path:name>")
@login_required
def uploads(name):
    u = current_user()
    if u["role"] == "Candidate":
        # Candidates may only load their own profile photo; documents go through candidate_document().
        con = db()
        own = con.execute("SELECT photo FROM candidates WHERE id=? OR lower(email)=lower(?)",
                          (u["candidate_id"] or -1, u["email"])).fetchone()
        con.close()
        if not own or own["photo"] != name:
            return "Not allowed", 403
    return send_from_directory(config.UPLOAD_DIR, name)
