"""Login, signup, password reset, two-factor sign-in and logout."""
import os
import re
import sqlite3
from datetime import datetime
from flask import Response, flash, jsonify, redirect, render_template, request, session, url_for
from io import BytesIO
from werkzeug.security import check_password_hash, generate_password_hash
from recruiter_ats.config import app, pyotp, qrcode
from recruiter_ats.database import db
from recruiter_ats.security import audit, current_user
from recruiter_ats.services.ai import ai_configured
from recruiter_ats.services.mailer import generate_temp_password, send_email


@app.route("/")
def index(): return redirect(url_for("dashboard") if current_user() else url_for("login"))

def finish_login(u):
    """Common post-credential-check login branching: 2FA challenge/enrollment, then dashboard."""
    if u["totp_enabled"] and pyotp:
        session.pop("user_id",None)
        session["pending_2fa_user_id"]=u["id"]
        session["pending_2fa_attempts"]=0
        return redirect(url_for("login_2fa"))
    # All non-Admin accounts must enroll in 2FA before their first successful login.
    # Admin accounts keep the existing optional/admin-managed 2FA behavior.
    if u["role"] != "Admin" and not u["totp_enabled"] and pyotp:
        session.pop("user_id",None)
        session["pending_2fa_setup_user_id"]=u["id"]
        session.pop("pending_2fa_setup_secret",None)
        return redirect(url_for("login_2fa_setup"))
    if u["totp_required"] and pyotp:
        session.pop("user_id",None)
        session["pending_2fa_setup_user_id"]=u["id"]
        session.pop("pending_2fa_setup_secret",None)
        return redirect(url_for("login_2fa_setup"))
    session["user_id"]=u["id"]; audit("LOGIN","USER",u["id"],"Successful login")
    return redirect(url_for("dashboard"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); pw=request.form.get("password","")
        con=db(); u=con.execute("SELECT * FROM users WHERE lower(email)=? AND active=1",(email,)).fetchone(); con.close()
        if not u or not check_password_hash(u["password_hash"],pw):
            flash("Invalid email or password.","danger"); return render_template("login.html")
        if u["must_change_password"]:
            session.pop("user_id",None)
            session["pending_pwchange_user_id"]=u["id"]
            return redirect(url_for("force_password_change"))
        return finish_login(u)
    return render_template("login.html")

@app.route("/signup",methods=["GET","POST"])
def signup():
    """Real email-based signup: the user only supplies their name and a real email address.
    A temporary password is generated on the server and emailed to that address via the
    SMTP server configured in Settings; nothing is ever shown on-screen. The user then signs
    in with that temporary password and is immediately required to set their own password."""
    if current_user(): return redirect(url_for("dashboard"))
    if request.method=="POST":
        name=request.form.get("name","").strip()
        email=request.form.get("email","").strip().lower()
        errors=[]
        if not name: errors.append("Please enter your full name.")
        if not email or "@" not in email or "." not in email.split("@")[-1]:
            errors.append("Please enter a valid email address.")
        if errors:
            for e in errors: flash(e,"danger")
            return render_template("signup.html", name=name, email=email)
        con=db()
        try:
            existing=con.execute("SELECT id FROM users WHERE lower(email)=?",(email,)).fetchone()
            if existing:
                con.close()
                flash("An account with this email already exists. Please sign in, or use \u201cForgot password\u201d if you don\u2019t remember it.","danger")
                return render_template("signup.html", name=name, email=email)
            temp_password=generate_temp_password()
            candidate=con.execute("SELECT id FROM candidates WHERE lower(email)=?",(email,)).fetchone()
            con.execute("""INSERT INTO users(name,email,password_hash,role,candidate_id,created_at,totp_required,must_change_password)
                           VALUES(?,?,?,?,?,?,1,1)""",
                        (name,email,generate_password_hash(temp_password),"Candidate",
                         candidate["id"] if candidate else None,datetime.now().isoformat(timespec="seconds")))
            new_id=con.execute("SELECT id FROM users WHERE lower(email)=?",(email,)).fetchone()["id"]
            ok, err = send_email(email, "Your Recruiter ARS account",
                f"Hi {name},\n\n"
                f"Your Recruiter ARS candidate account has been created.\n\n"
                f"Email: {email}\n"
                f"Temporary password: {temp_password}\n\n"
                f"Sign in at your organization's Recruiter ARS login page using this temporary "
                f"password. You will be asked to set your own password immediately after signing in.\n\n"
                f"If you did not request this account, you can ignore this email.")
            if not ok:
                # Don't leave an account whose only password was never delivered.
                con.execute("DELETE FROM users WHERE id=?", (new_id,))
                con.commit(); con.close()
                flash(f"We couldn't send the signup email ({err}). Please try again later or contact an administrator.","danger")
                return render_template("signup.html", name=name, email=email)
            con.commit()
            audit("SIGNUP","USER",new_id,"Self-registered candidate account; temporary password emailed")
            con.close()
            flash("Account created. Check your email for a temporary password, then sign in below.","success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            con.close(); flash("An account with this email already exists. Please sign in instead.","danger")
            return render_template("signup.html", name=name, email=email)
    return render_template("signup.html")

@app.route("/forgot-password",methods=["GET","POST"])
def forgot_password():
    if current_user(): return redirect(url_for("dashboard"))
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        if not email or "@" not in email:
            flash("Please enter a valid email address.","danger")
            return render_template("forgot_password.html", email=email)
        con=db(); u=con.execute("SELECT * FROM users WHERE lower(email)=? AND active=1",(email,)).fetchone()
        if u:
            temp_password=generate_temp_password()
            con.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                        (generate_password_hash(temp_password), u["id"]))
            ok, err = send_email(email, "Your Recruiter ARS password reset",
                f"Hi {u['name']},\n\n"
                f"We received a request to reset the password on your Recruiter ARS account.\n\n"
                f"Temporary password: {temp_password}\n\n"
                f"Sign in with this temporary password. You will be asked to set your own new "
                f"password immediately after signing in.\n\n"
                f"If you did not request this, please contact an administrator \u2014 your old "
                f"password no longer works.")
            if ok:
                con.commit()
                audit("PASSWORD_RESET_REQUEST","USER",u["id"],"Temporary password emailed")
            con.close()
        else:
            con.close()
        # Same message whether or not the account exists, so the form can't be used to find out
        # which emails are registered.
        flash("If that email is registered, we've sent a temporary password to it.","success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")

@app.route("/account/force-password-change",methods=["GET","POST"])
def force_password_change():
    uid=session.get("pending_pwchange_user_id")
    if not uid: return redirect(url_for("login"))
    con=db(); u=con.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); con.close()
    if not u or not u["must_change_password"]:
        session.pop("pending_pwchange_user_id",None)
        return redirect(url_for("login"))
    if request.method=="POST":
        password=request.form.get("password",""); confirm=request.form.get("confirm_password","")
        errors=[]
        if len(password) < 8: errors.append("Password must be at least 8 characters long.")
        if password != confirm: errors.append("Passwords do not match.")
        if u["voice_pw_hash"] and check_password_hash(u["voice_pw_hash"], password): errors.append("Your login password must be different from your voice-command password.")
        if errors:
            for e in errors: flash(e,"danger")
            return render_template("force_password_change.html", user_name=u["name"])
        con=db()
        con.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                    (generate_password_hash(password), u["id"]))
        con.commit(); con.close()
        session.pop("pending_pwchange_user_id",None)
        audit("PASSWORD_CHANGED","USER",u["id"],"Password set after temporary-password sign-in")
        flash("Password updated.","success")
        return finish_login(u)
    return render_template("force_password_change.html", user_name=u["name"])

@app.route("/login/2fa",methods=["GET","POST"])
def login_2fa():
    uid=session.get("pending_2fa_user_id")
    if not uid: return redirect(url_for("login"))
    con=db(); u=con.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); con.close()
    if not u or not u["totp_enabled"]:
        session.pop("pending_2fa_user_id",None); return redirect(url_for("login"))
    if request.method=="POST":
        code=re.sub(r"\D","",request.form.get("code",""))
        totp=pyotp.TOTP(u["totp_secret"])
        if code and totp.verify(code, valid_window=1):
            session.pop("pending_2fa_user_id",None); session.pop("pending_2fa_attempts",None)
            session["user_id"]=u["id"]; audit("LOGIN","USER",u["id"],"Successful login (2FA verified)")
            return redirect(url_for("dashboard"))
        session["pending_2fa_attempts"]=session.get("pending_2fa_attempts",0)+1
        audit("LOGIN_2FA_FAILED","USER",u["id"],f"Attempt {session['pending_2fa_attempts']}")
        if session["pending_2fa_attempts"]>=5:
            session.pop("pending_2fa_user_id",None); session.pop("pending_2fa_attempts",None)
            flash("Too many incorrect codes. Please sign in again.","danger")
            return redirect(url_for("login"))
        flash("Incorrect code. Please try again.","danger")
    return render_template("login_2fa.html",user_name=u["name"])

@app.route("/login/2fa/cancel")
def login_2fa_cancel():
    session.pop("pending_2fa_user_id",None); session.pop("pending_2fa_attempts",None)
    return redirect(url_for("login"))

@app.route("/login/2fa-setup",methods=["GET","POST"])
def login_2fa_setup():
    """Mandatory enrollment step for accounts an Admin has flagged as requiring 2FA."""
    uid=session.get("pending_2fa_setup_user_id")
    if not uid: return redirect(url_for("login"))
    con=db(); u=con.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); con.close()
    # Non-Admin accounts must always enroll (see finish_login), even when the Admin has not flagged them.
    needs_enrollment = bool(u and (u["totp_required"] or u["role"] != "Admin"))
    if not u or not needs_enrollment or u["totp_enabled"]:
        session.pop("pending_2fa_setup_user_id",None); session.pop("pending_2fa_setup_secret",None)
        return redirect(url_for("login"))
    if not pyotp:
        flash("Two-factor authentication is required on this account but the server is missing required libraries. Contact an administrator.","danger")
        return redirect(url_for("login"))
    if "pending_2fa_setup_secret" not in session:
        session["pending_2fa_setup_secret"]=pyotp.random_base32()
    secret=session["pending_2fa_setup_secret"]
    if request.method=="POST":
        code=re.sub(r"\D","",request.form.get("code",""))
        if code and pyotp.TOTP(secret).verify(code, valid_window=1):
            con=db(); con.execute("UPDATE users SET totp_secret=?,totp_enabled=1 WHERE id=?",(secret,u["id"])); con.commit(); con.close()
            session.pop("pending_2fa_setup_user_id",None); session.pop("pending_2fa_setup_secret",None)
            session["user_id"]=u["id"]
            audit("2FA_ENABLED","USER",u["id"],"Enrolled to satisfy administrator requirement")
            audit("LOGIN","USER",u["id"],"Successful login")
            return redirect(url_for("dashboard"))
        flash("Incorrect code. Please try again.","danger")
    return render_template("login_2fa_setup.html",user_name=u["name"],secret=secret)

@app.route("/login/2fa-setup/qrcode.png")
def login_2fa_setup_qrcode():
    uid=session.get("pending_2fa_setup_user_id")
    secret=session.get("pending_2fa_setup_secret")
    if not uid or not secret or not qrcode: return "Not found",404
    con=db(); u=con.execute("SELECT email FROM users WHERE id=?",(uid,)).fetchone(); con.close()
    if not u: return "Not found",404
    uri=pyotp.totp.TOTP(secret).provisioning_uri(name=u["email"], issuer_name="Recruiter ARS")
    img=qrcode.make(uri)
    buf=BytesIO(); img.save(buf,format="PNG"); buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/login/2fa-setup/cancel")
def login_2fa_setup_cancel():
    session.pop("pending_2fa_setup_user_id",None); session.pop("pending_2fa_setup_secret",None)
    return redirect(url_for("login"))

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/health")
def health():
    con=db()
    con.execute("SELECT 1"); con.close()
    return jsonify(ok=True,database="connected",ai_configured=ai_configured())
