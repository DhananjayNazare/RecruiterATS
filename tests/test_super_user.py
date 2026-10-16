"""The hidden backend super user and the SMTP settings only it may see."""
from recruiter_ats.database import db, get_setting, ensure_super_user


def super_id():
    con = db()
    row = con.execute("SELECT id FROM users WHERE is_super=1").fetchone()
    con.close()
    return row["id"]


def test_exactly_one_super_user_exists(env):
    con = db()
    assert con.execute("SELECT COUNT(*) c FROM users WHERE is_super=1").fetchone()["c"] == 1
    con.close()


def test_super_user_is_not_created_twice(env):
    con = db()
    ensure_super_user(con)
    assert con.execute("SELECT COUNT(*) c FROM users WHERE is_super=1").fetchone()["c"] == 1
    con.close()


def test_super_user_is_hidden_from_the_admin_panel(admin):
    page = admin.get("/admin").get_data(as_text=True)
    assert "super@test.local" not in page and "System Super User" not in page


def test_admin_cannot_touch_the_super_user(admin):
    sid = super_id()
    assert admin.get(f"/admin/users/{sid}/edit").status_code == 302
    admin.post("/admin/users/delete", user_id=sid)
    admin.post("/admin/users/toggle", user_id=sid)
    con = db()
    row = con.execute("SELECT active FROM users WHERE id=?", (sid,)).fetchone()
    con.close()
    assert row is not None and row["active"] == 1


def test_super_user_can_log_in_and_use_the_admin_panel(superuser):
    assert superuser.get("/admin").status_code == 200


def test_smtp_section_is_visible_to_the_super_user_only(admin, superuser, recruiter):
    assert "SMTP Host" in superuser.get("/settings").get_data(as_text=True)
    assert "SMTP" not in admin.get("/settings").get_data(as_text=True)
    assert "SMTP" not in recruiter.get("/settings").get_data(as_text=True)


def test_only_the_super_user_can_save_smtp_settings(admin, superuser):
    admin.post("/settings", form_type="smtp", smtp_host="evil.example.com")
    assert get_setting("smtp_host", "") == ""
    superuser.post("/settings", form_type="smtp", smtp_host="smtp.example.com")
    assert get_setting("smtp_host", "") == "smtp.example.com"


def test_smtp_test_email_is_super_user_only(admin):
    response = admin.post("/settings/smtp/test")
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")


def test_backup_settings_are_admin_only(admin, recruiter, interviewer):
    for client in (recruiter, interviewer):
        client.post("/settings", form_type="backup", backup_frequency="Hourly")
    assert get_setting("backup_frequency", "unset") == "unset"
    admin.post("/settings", form_type="backup", backup_frequency="Weekly")
    assert get_setting("backup_frequency", "unset") == "Weekly"
