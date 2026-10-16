"""The everyday recruiter workflow, plus the new popup forms."""
from recruiter_ats import config
from recruiter_ats import config
from recruiter_ats.database import db


def count(table):
    con = db()
    n = con.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    con.close()
    return n


def test_positions_page_has_a_create_button_with_a_popup(admin):
    page = admin.get("/jobs").get_data(as_text=True)
    assert 'data-bs-target="#createPositionModal"' in page
    assert 'id="createPositionModal"' in page


def test_teams_page_has_a_create_company_popup(admin):
    page = admin.get("/operations").get_data(as_text=True)
    assert 'data-bs-target="#createCompanyModal"' in page
    assert 'id="createCompanyModal"' in page


def test_create_position(admin):
    admin.post("/jobs/add", title="Data Analyst", description="SQL", category_name="Data", location="Pune")
    assert count("jobs") == 1


def test_position_needs_a_description_or_jd_file(admin):
    admin.post("/jobs/add", title="Empty")
    assert count("jobs") == 0


def test_create_company(admin):
    admin.post("/operations/add/companies", name="Globex", industry="Energy")
    assert count("companies") >= 1
    con = db()
    assert con.execute("SELECT 1 FROM companies WHERE name='Globex'").fetchone()
    con.close()


def test_full_hiring_flow(sample, admin):
    assert count("applications") == 1 and count("interviews") == 1
    assert admin.get(f"/candidates/{sample['candidate']}").status_code == 200
    assert admin.get(f"/meeting/{sample['interview']}").status_code == 200
    admin.post(f"/applications/{sample['application']}/status", status="Interview")
    admin.post(f"/jobs/{sample['job']}/delete")
    assert count("jobs") == 0 and count("applications") == 0


def test_calendar_close_button_has_no_inline_quote_bug(admin, sample):
    page = admin.get("/calendar").get_data(as_text=True)
    assert "data-detail-close" in page
    assert 'onclick="this.closest' not in page


def test_search_box_text_is_black():
    css = (config.BASE_DIR / "static" / "ui_template.css").read_text()
    assert "color:#000!important" in css


def test_interview_add_rejects_bad_application_and_date(sample, admin):
    """Bad input flashes a message instead of a 500 (and no row is created)."""
    from recruiter_ats.database import db
    for data in ({"application_id": "99999", "scheduled_at": "2030-01-01T10:00"},
                 {"application_id": str(sample["application"]), "scheduled_at": "not-a-date"},
                 {"application_id": "abc", "scheduled_at": "2030-01-01T10:00"}):
        r = admin.post("/interviews/add", **data)
        assert r.status_code in (302, 303), (data, r.status_code)
    con = db(); n = con.execute("SELECT COUNT(*) FROM interviews").fetchone()[0]; con.close()
    assert n == 1                                   # only the one from the fixture


def test_candidates_list_shows_10_then_more(env, admin):
    from recruiter_ats.database import db
    con = db()
    con.executemany("INSERT INTO candidates(name,email,created_at) VALUES(?,?,'2026-01-01')", [(f"Pg {i:03d}", f"p{i}@x.com") for i in range(230)])
    con.commit(); con.close()
    p1 = admin.get("/candidates").get_data(as_text=True)
    assert "Pg 229" in p1 and "Pg 220" in p1 and "Pg 219" not in p1 and "Showing 10 of 230" in p1
    p2 = admin.get("/candidates?shown=20").get_data(as_text=True)
    assert "Pg 210" in p2 and "Pg 209" not in p2 and "(230)" in p2
    assert admin.get("/candidates?shown=999999").status_code == 200 and admin.get("/candidates?shown=x").status_code == 200


def test_interviews_page_first_10_rows_marked(sample, admin):
    page = admin.get("/interviews").get_data(as_text=True)
    assert 'class="iv-row"' in page


def test_candidates_search_by_ai_score_threshold(sample, admin):
    from recruiter_ats.database import db
    con = db()
    con.execute("INSERT INTO candidates(name,email,created_at) VALUES('High Score','h@x.com','2026-01-01'),('Low Score','l@x.com','2026-01-01')")
    hi, lo = [r[0] for r in con.execute("SELECT id FROM candidates WHERE name IN ('High Score','Low Score') ORDER BY name").fetchall()]
    con.execute("INSERT INTO applications(candidate_id,job_id,status,match_score) VALUES(?,?,?,?)", (hi, sample["job"], "Applied", 85))
    con.execute("INSERT INTO applications(candidate_id,job_id,status,match_score) VALUES(?,?,?,?)", (lo, sample["job"], "Applied", 40))
    con.commit(); con.close()
    page = admin.get("/candidates?score=70").get_data(as_text=True)
    assert "High Score" in page and "Low Score" not in page and "Ann Applicant" not in page     # Ann has no score
    assert "Low Score" in admin.get("/candidates?score=30").get_data(as_text=True)
    assert "Low Score" in admin.get("/candidates").get_data(as_text=True)                       # no threshold = everyone
    assert admin.get("/candidates?score=abc").status_code == 200 and admin.get("/candidates?score=500").status_code == 200


def test_team_companies_use_candidate_card(sample, admin):
    page = admin.get("/operations").get_data(as_text=True)
    assert "candidate-card candidate-card-professional" in page and "Acme" in page
