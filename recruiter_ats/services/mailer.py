"""Sending email (SMTP), the scheduled-email worker and password helpers."""
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from recruiter_ats.database import db, get_setting


def get_smtp_config():
    return {
        "host": get_setting("smtp_host", ""),
        "port": get_setting("smtp_port", "587"),
        "username": get_setting("smtp_username", ""),
        "password": get_setting("smtp_password", ""),
        "use_tls": get_setting("smtp_use_tls", "1") == "1",
        "from_email": get_setting("smtp_from_email", ""),
        "from_name": get_setting("smtp_from_name", "Recruiter ARS"),
    }

def send_email(to_addr, subject, body):
    """Send a plain-text email using the SMTP settings saved on the Settings page.
    Returns (ok, error_message)."""
    cfg = get_smtp_config()
    if not cfg["host"] or not cfg["from_email"]:
        return False, "Email server is not configured yet. Please contact the system administrator."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_email"]}>' if cfg["from_name"] else cfg["from_email"]
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        port = int(cfg["port"] or 587)
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=ssl.create_default_context(), timeout=15) as server:
                if cfg["username"]:
                    server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=15) as server:
                if cfg["use_tls"]:
                    server.starttls(context=ssl.create_default_context())
                if cfg["username"]:
                    server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

def send_bulk_email_bcc(recipients, subject, body):
    """Send one plain-text email with all recipients in BCC.
    Returns (ok, error_message). The BCC list is never exposed to recipients."""
    cfg = get_smtp_config()
    if not cfg["host"] or not cfg["from_email"]:
        return False, "Email server is not configured yet. Please contact the system administrator."
    recipients = [str(x).strip() for x in recipients if str(x).strip()]
    if not recipients:
        return False, "No recipient email addresses were supplied."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_email"]}>' if cfg["from_name"] else cfg["from_email"]
    # Keep the visible To header on the sender account; candidate addresses remain BCC-only.
    msg["To"] = cfg["from_email"]
    msg["Bcc"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        port = int(cfg["port"] or 587)
        if port == 465:
            with smtplib.SMTP_SSL(cfg["host"], port, context=ssl.create_default_context(), timeout=15) as server:
                if cfg["username"]:
                    server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], port, timeout=15) as server:
                if cfg["use_tls"]:
                    server.starttls(context=ssl.create_default_context())
                if cfg["username"]:
                    server.login(cfg["username"], cfg["password"])
                server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

def generate_temp_password(length=10):
    """Generate a random temporary password (mixed case letters + digits, no ambiguous characters)."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))

def email_worker():
    """Sends scheduled candidate/interviewer status-update emails once their scheduled time arrives."""
    while True:
        try:
            now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M")
            con = db()
            due = con.execute("SELECT * FROM email_schedules WHERE status='Pending' AND scheduled_at<=?", (now_iso,)).fetchall()
            for row in due:
                ok, err = send_email(row["recipient_email"], row["subject"], row["message"])
                if ok:
                    con.execute("UPDATE email_schedules SET status='Sent',sent_at=?,error=NULL WHERE id=?",
                                (datetime.now().isoformat(timespec="seconds"), row["id"]))
                else:
                    con.execute("UPDATE email_schedules SET status='Failed',error=? WHERE id=?", (err, row["id"]))
                con.commit()
            con.close()
        except Exception:
            pass
        time.sleep(30)

def queue_email(con, application_id, recipient_type, name, email, subject, body, actor_id=None):
    """Put an email on the existing email scheduler (sent by email_worker within ~30 seconds)."""
    now = datetime.now()
    con.execute("""INSERT INTO email_schedules(application_id,recipient_type,recipient_name,recipient_email,subject,message,scheduled_at,status,created_by,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (application_id, recipient_type, name, email, subject, body,
                 now.strftime("%Y-%m-%dT%H:%M"), "Pending", actor_id, now.isoformat(timespec="seconds")))
