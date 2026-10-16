"""Sign-in helpers, permission decorators, CSRF protection and the central access rules."""
import uuid
from datetime import datetime
from flask import flash, g, jsonify, redirect, request, session, url_for
from functools import wraps
from recruiter_ats.config import PERMISSIONS, app
from recruiter_ats.database import db


def current_user():
    uid = session.get("user_id")
    if not uid: return None
    cached = g.get("_cu")                       # one lookup per request instead of one per call
    if cached is not None and cached[0] == uid: return cached[1]
    con=db(); u=con.execute("SELECT * FROM users WHERE id=? AND active=1",(uid,)).fetchone(); con.close()
    g._cu = (uid, u)
    return u

def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not current_user(): return redirect(url_for("login"))
        return fn(*a,**kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u: return redirect(url_for("login"))
        if u["role"]!="Admin":
            flash("Administrator access required.","danger"); return redirect(url_for("dashboard"))
        return fn(*a,**kw)
    return wrapper

def is_super_user(u=None):
    u = u or current_user()
    return bool(u and u["is_super"])

def super_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u: return redirect(url_for("login"))
        if not u["is_super"]:
            flash("You don't have access to that page.","danger"); return redirect(url_for("dashboard"))
        return fn(*a,**kw)
    return wrapper

def has_permission(permission):
    u=current_user()
    if not u: return False
    if u["role"] == "Admin": return True
    con=db(); row=con.execute("SELECT granted FROM user_permissions WHERE user_id=? AND permission=?", (u["id"], permission)).fetchone(); con.close()
    return bool(row and row["granted"])

def permission_required(permission):
    """Require a specific granted permission (Admins always pass via has_permission)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*a,**kw):
            u=current_user()
            if not u: return redirect(url_for("login"))
            if u["role"] in {"Candidate","Interviewer"} or not has_permission(permission):
                label=next((lbl for key,lbl,_ in PERMISSIONS if key==permission), permission)
                msg=f"You don't have the '{label}' permission for this action. Ask an administrator to grant it."
                if request.is_json or request.path.startswith("/ai") :
                    return jsonify(ok=False,error=msg),403
                flash(msg,"danger")
                return redirect(request.referrer or url_for("dashboard"))
            return fn(*a,**kw)
        return wrapper
    return decorator

def staff_only(fn):
    """Block Candidate and Interviewer accounts from internal recruiter/admin pages."""
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u: return redirect(url_for("login"))
        if u["role"] in {"Candidate","Interviewer"}:
            return redirect(url_for("candidate_panel" if u["role"]=="Candidate" else "interviewer_panel"))
        return fn(*a,**kw)
    return wrapper

def user_management_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        u=current_user()
        if not u: return redirect(url_for("login"))
        if not has_permission("manage_users"):
            flash("User-management permission is required.", "danger"); return redirect(url_for("dashboard"))
        return fn(*a,**kw)
    return wrapper

def audit(action, entity, entity_id=None, details="", actor_id=None):
    uid = actor_id
    if uid is None:
        try:
            u=current_user(); uid = u["id"] if u else None
        except RuntimeError:   # called from a background thread (no request/session)
            uid = None
    con=db(); con.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
                          (uid,action,entity,entity_id,details,datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = uuid.uuid4().hex
        session["_csrf_token"] = token
    return token

@app.before_request
def csrf_protect():
    if request.method != "POST" or request.endpoint in {"login"}:
        return None
    expected = session.get("_csrf_token")
    supplied = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or supplied != expected:
        if request.is_json:
            return jsonify(ok=False, error="Invalid CSRF token."), 400
        return "Invalid CSRF token.", 400
    return None

# --- Central access rules -------------------------------------------------
# Every request passes through enforce_access() before reaching a view. Candidates and
# Interviewers get an explicit allow-list, so a new route is closed to them by default
# instead of depending on someone remembering to add a decorator.
PUBLIC_ENDPOINTS = {
    "static", "index", "login", "signup", "forgot_password", "force_password_change",
    "login_2fa", "login_2fa_cancel", "login_2fa_setup", "login_2fa_setup_qrcode",
    "login_2fa_setup_cancel", "logout", "health",
}

# Pages any signed-in user may use (own 2FA settings, shared files and meetings).
SHARED_ENDPOINTS = {
    "logout", "dashboard", "settings", "twofa_setup", "twofa_confirm", "twofa_cancel_setup",
    "twofa_disable", "twofa_qrcode", "uploads", "meeting_room",
}

PORTAL_ENDPOINTS = {
    "Candidate": {"candidate_panel", "candidate_apply", "candidate_upload_documents", "candidate_document",
                  "candidate_schedule_ai_interview", "candidate_ai_interview", "candidate_ai_interview_start",
                  "candidate_ai_interview_answer", "candidate_ai_interview_submit"},
    "Interviewer": {"interviewer_panel", "interviewer_interview", "interviewer_resume", "interviewer_ai_assistant_page", "ai_interview_assistant"},
}

PORTAL_HOME = {"Candidate": "candidate_panel", "Interviewer": "interviewer_panel"}

@app.before_request
def enforce_access():
    endpoint = request.endpoint
    if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
        return None
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    role = user["role"]
    if role in PORTAL_ENDPOINTS and endpoint not in SHARED_ENDPOINTS | PORTAL_ENDPOINTS[role]:
        if request.is_json or request.method != "GET":
            return jsonify(ok=False, error="Access denied for your account type."), 403
        return redirect(url_for(PORTAL_HOME[role]))
    return None

@app.context_processor
def inject_csrf():
    return {"csrf_token": csrf_token()}
