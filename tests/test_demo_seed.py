"""The demo-data script loads 1000 candidates and every main screen still renders with them."""
import sys
from conftest import ROOT
sys.path.insert(0, str(ROOT))
import seed_demo_data

def test_demo_seed_and_pages(env, admin):
    seed_demo_data.main()
    from recruiter_ats.database import db
    con = db()
    assert con.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE '%@demo.example'").fetchone()[0] == 1000
    assert con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1000
    con.close()
    for url in ("/candidates", "/applications", "/dashboard", "/interviews", "/jobs", "/reports"):
        r = admin.get(url)
        assert r.status_code == 200, url
    assert b"btn-add-candidate" in admin.get("/candidates").data
    assert b"rust_ui.css" in admin.get("/candidates").data


def test_settings_demo_button_loads_and_removes(env, admin):
    from recruiter_ats.database import db
    def n():
        con = db(); v = con.execute("SELECT COUNT(*) FROM candidates WHERE email LIKE '%@demo.example'").fetchone()[0]; con.close(); return v
    assert admin.post("/settings/demo-data", action="load", count="120").status_code == 302 and n() == 120
    assert admin.post("/settings/demo-data", action="load", count="50").status_code == 302 and n() == 50     # replaced, not stacked
    assert admin.post("/settings/demo-data", action="remove").status_code == 302 and n() == 0
