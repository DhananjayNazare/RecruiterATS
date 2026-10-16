import re
from tests.conftest import *  # noqa
def test_dash(admin):
    r=admin.get("/dashboard"); h=r.get_data(as_text=True)
    assert r.status_code==200
    for k in ["batchResumeModal","todosModal","emailSchedulerModal","batchWidget"]: assert k in h
    tok=re.search(r'name="_csrf_token" value="([^"]+)"',h).group(1)
    r=admin.post("/todos/add",title="X"); assert r.headers["Location"].endswith("/dashboard")
    assert "X" in admin.get("/dashboard").get_data(as_text=True)
    assert "createCompanyModal" in admin.get("/operations").get_data(as_text=True)
    o=admin.get("/operations").get_data(as_text=True); assert "Status Update Email Scheduler" not in o and "To-Dos" not in o
    assert "Batch Resume Processing" not in admin.get("/candidates").get_data(as_text=True)
    assert admin.get("/dashboard?open=batch&position=1").status_code==200


def test_theme_toggle(admin, env):
    h=admin.get("/dashboard").get_data(as_text=True)
    assert "themeToggleBtn" in h and "dark.css" in h and "data-bs-theme" in h
    assert "themeToggleBtn" in env.test_client().get("/login").get_data(as_text=True)
    assert admin.get("/static/dark.css").status_code==200


def test_dashboard_position_calendar_todo_edit_back(admin, env):
    h=admin.get("/dashboard").get_data(as_text=True)
    assert "createPositionModal" in h and "calendarModal" not in h and "createInterviewModal" in h and "addApplicationModal" in h
    admin.post("/todos/add",title="Old")
    import re
    h=admin.get("/dashboard").get_data(as_text=True)
    tid=re.search(r'/todos/(\d+)/edit',h).group(1)
    r=admin.post(f"/todos/{tid}/edit",title="New",due_date="2026-10-20",status="Done")
    assert r.status_code==302
    h=admin.get("/dashboard").get_data(as_text=True)
    assert "New" in h and "Done" in h
    assert "rt-back-btn" in admin.get("/candidates").get_data(as_text=True)
    assert "rt-back-btn" not in h
    assert "rt-back-float" in env.test_client().get("/forgot-password").get_data(as_text=True)


def test_quick_actions_dropdown(admin):
    h=admin.get("/dashboard").get_data(as_text=True)
    assert "Quick Actions" in h and "View Calendar" in h and "dropdown-menu" in h
