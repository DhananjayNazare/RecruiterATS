"""Who may open what. These tests guard the permission rules."""
import pytest

from recruiter_ats import app, config
from recruiter_ats.database import db
from recruiter_ats.security import PUBLIC_ENDPOINTS

STAFF_PAGES = ["/jobs", "/candidates", "/applications", "/ai", "/interviews", "/calendar",
               "/operations", "/reports", "/integrations", "/admin"]


def concrete_get_urls():
    """Every GET route, with dummy ids filled in for routes that take arguments."""
    urls = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        url = str(rule)
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", "1").replace(f"<path:{arg}>", "x.txt").replace(f"<{arg}>", "x")
        urls.append((rule.endpoint, url))
    return urls


def test_anonymous_visitors_are_sent_to_login(env):
    http = app.test_client()
    for endpoint, url in concrete_get_urls():
        if endpoint in PUBLIC_ENDPOINTS:
            continue
        response = http.get(url)
        assert response.status_code == 302 and "/login" in response.headers["Location"], url


@pytest.mark.parametrize("role", ["admin", "superuser", "recruiter", "interviewer", "candidate"])
def test_no_page_crashes_for_any_role(role, sample, login_as):
    client = {"admin": "admin@ars.local", "superuser": "super@test.local",
              "recruiter": "recruiter@test.local", "interviewer": "interviewer@test.local",
              "candidate": "candidate@test.local"}[role]
    client = login_as(client)
    for endpoint, url in concrete_get_urls():
        if endpoint in ("logout", "login_2fa_setup_qrcode", "twofa_qrcode"):
            continue
        assert client.get(url).status_code < 500, f"{role} {url}"


@pytest.mark.parametrize("who", ["candidate", "interviewer"])
def test_portal_users_cannot_open_staff_pages(who, sample, candidate, interviewer):
    client = {"candidate": candidate, "interviewer": interviewer}[who]
    home = "/candidate-panel" if who == "candidate" else "/interviewer-panel"
    for url in STAFF_PAGES + [f"/candidates/{sample['candidate']}"]:
        response = client.get(url)
        assert response.status_code == 302 and response.headers["Location"].endswith(home), url


def test_portal_users_cannot_post_to_staff_actions(sample, candidate, interviewer):
    for client in (candidate, interviewer):
        assert client.post("/jobs/add", title="Hack", description="x").status_code == 403
        assert client.post(f"/candidates/{sample['candidate']}/delete").status_code == 403
        assert client.post("/admin/users/add", name="x", email="x@x.com", password="x").status_code == 403


def test_portal_users_see_their_own_portal(sample, candidate, interviewer):
    assert candidate.get("/candidate-panel").status_code == 200
    assert interviewer.get("/interviewer-panel").status_code == 200
    assert interviewer.get(f"/interviewer-panel/interview/{sample['interview']}").status_code == 200


def test_portals_have_a_logout_button(candidate, interviewer):
    assert "/logout" in candidate.get("/candidate-panel").get_data(as_text=True)
    assert "/logout" in interviewer.get("/interviewer-panel").get_data(as_text=True)


def test_recruiter_without_permission_cannot_create_positions(recruiter):
    response = recruiter.post("/jobs/add", title="Nope", description="x")
    assert response.status_code == 302
    con = db()
    assert con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0
    con.close()


def test_candidate_can_only_load_their_own_upload(env, candidate, admin):
    (config.UPLOAD_DIR / "someone_elses_resume.pdf").write_bytes(b"secret")
    assert candidate.get("/uploads/someone_elses_resume.pdf").status_code == 403
    assert admin.get("/uploads/someone_elses_resume.pdf").status_code == 200
