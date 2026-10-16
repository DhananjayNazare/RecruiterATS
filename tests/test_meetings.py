"""Meetings: Teams / Google Meet / Zoom / Custom links only; the built-in ATS meeting room is gone."""
from recruiter_ats.database import db
from recruiter_ats.services.scheduling import clean_meeting_link, clean_provider, sched_config


def test_provider_and_link_cleaning():
    assert clean_provider("Google Meet") == "Google Meet"
    assert clean_provider("Recruiter ARS Meeting") == "Microsoft Teams"   # legacy value maps to the default
    assert clean_meeting_link("") == ""
    assert clean_meeting_link("https://meet.google.com/abc-defg-hij") == "https://meet.google.com/abc-defg-hij"
    for bad in ("javascript:alert(1)", "meet.google.com/x", "https://a b", "ftp://x.y/z"):
        assert clean_meeting_link(bad) is None


def test_forms_offer_only_external_providers(admin):
    for url in ("/interviews", "/dashboard"):
        page = admin.get(url).get_data(as_text=True)
        assert "Recruiter ARS Meeting" not in page
        for p in ("Microsoft Teams", "Google Meet", "Zoom", "Custom Meeting"):
            assert f"<option>{p}</option>" in page


def test_add_and_change_meeting_link(admin, sample):
    con = db(); row = con.execute("SELECT meeting_provider,meeting_link FROM interviews").fetchone(); con.close()
    assert row["meeting_provider"] == "Microsoft Teams" and row["meeting_link"].startswith("https://teams.")
    r = admin.post(f"/interviews/{sample['interview']}/meeting", meeting_provider="Google Meet", meeting_link="https://meet.google.com/abc-defg-hij")
    assert r.status_code == 302
    con = db(); row = con.execute("SELECT meeting_provider,meeting_link FROM interviews").fetchone(); con.close()
    assert (row["meeting_provider"], row["meeting_link"]) == ("Google Meet", "https://meet.google.com/abc-defg-hij")
    admin.post(f"/interviews/{sample['interview']}/meeting", meeting_provider="Zoom", meeting_link="javascript:alert(1)")
    con = db(); row = con.execute("SELECT meeting_provider,meeting_link FROM interviews").fetchone(); con.close()
    assert row["meeting_provider"] == "Google Meet"          # invalid link rejected, nothing changed
    page = admin.get("/interviews").get_data(as_text=True)
    assert "https://meet.google.com/abc-defg-hij" in page and f"meetingModal{sample['interview']}" in page


def test_auto_schedule_uses_default_meeting_link(admin, sample, env):
    from recruiter_ats.database import set_setting
    set_setting("sched_provider", "Zoom"); set_setting("sched_meeting_link", "https://zoom.us/j/123")
    cfg = sched_config()
    assert cfg["provider"] == "Zoom" and cfg["meeting_link"] == "https://zoom.us/j/123"
    set_setting("sched_provider", "Recruiter ARS Meeting")
    assert sched_config()["provider"] == "Microsoft Teams"


def test_legacy_builtin_meetings_are_migrated(env):
    from recruiter_ats import init_db
    con = db()
    con.execute("INSERT INTO candidates(name) VALUES('X')"); con.execute("INSERT INTO jobs(title,description) VALUES('J','d')")
    con.execute("INSERT INTO applications(candidate_id,job_id) VALUES(1,1)")
    con.execute("INSERT INTO interviews(application_id,meeting_provider,meeting_link) VALUES(1,'Recruiter ARS Meeting','http://x/meeting/1?token=a')")
    con.commit(); con.close()
    init_db()
    con = db(); row = con.execute("SELECT meeting_provider,meeting_link FROM interviews").fetchone(); con.close()
    assert (row["meeting_provider"], row["meeting_link"]) == ("Custom Meeting", "")


def test_meeting_room_page_has_no_embedded_video(admin, sample):
    page = admin.get(f"/meeting/{sample['interview']}").get_data(as_text=True)
    assert "meet.jit.si" not in page and "<iframe" not in page
    assert "https://teams.microsoft.com/l/meetup-join/abc" in page
