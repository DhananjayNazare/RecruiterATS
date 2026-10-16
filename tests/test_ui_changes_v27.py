"""Sidebar/navbar, real dashboard percentages, Jitsi clean-up and the 5-per-row candidates grid."""
from datetime import datetime, timedelta
from recruiter_ats.database import db, init_db


def test_user_and_logoff_live_in_navbar_not_sidebar(env, admin):
    html = admin.get("/dashboard").get_data(as_text=True)
    side = html[html.index('<aside class="rt-sidebar"'):html.index("</aside>")]
    assert "rt-side-user" not in side and "Administrator" not in side and "logout" not in side
    top = html[html.index('<header class="rt-topbar"'):html.index("</header>")]
    assert "Administrator" in top and 'class="rt-logout"' in top and "/logout" in top


def _month(offset):
    first = datetime.now().replace(day=1, hour=12, minute=0, second=0, microsecond=0)
    for _ in range(offset):
        first = (first - timedelta(days=1)).replace(day=1)
    return first.isoformat(timespec="seconds")


def test_dashboard_percentages_are_real_not_hardcoded(env, admin):
    con = db()
    for i in range(3):   # last month: 2 candidates, this month: 3  -> +50%
        con.execute("INSERT INTO candidates(name,email,created_at) VALUES(?,?,?)", (f"L{i}", f"l{i}@x.io", _month(1)))
    for i in range(3):
        con.execute("INSERT INTO candidates(name,email,created_at) VALUES(?,?,?)", (f"T{i}", f"t{i}@x.io", _month(0)))
    con.execute("DELETE FROM candidates WHERE name='L2'")
    con.commit(); con.close()
    html = admin.get("/dashboard").get_data(as_text=True)
    assert "50% <span>vs last month</span>" in html and "This month: 3 \u00b7 Last month: 2" in html
    for fake in ("12% <span", "8% <span", "15% <span", "5% <span"):
        assert fake not in html
    assert "No change <span>vs last month</span>" in html     # nothing in either month -> flat, not a made-up number


def test_jitsi_links_are_cleared_on_startup(env, sample):
    con = db()
    con.execute("UPDATE interviews SET meeting_provider='Jitsi', meeting_link='https://meet.jit.si/demo-1'")
    con.commit(); con.close()
    init_db()
    con = db(); row = con.execute("SELECT meeting_provider,meeting_link FROM interviews").fetchone(); con.close()
    assert row["meeting_provider"] == "Custom Meeting" and row["meeting_link"] == ""


def test_candidates_grid_is_five_per_row(env, admin):
    css = open("static/rust_ui.css", encoding="utf-8").read()
    assert ".rt-content .candidate-grid{display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr))!important" in css
    assert 'class="candidate-grid"' in admin.get("/candidates").get_data(as_text=True) or "No candidates" in admin.get("/candidates").get_data(as_text=True)
