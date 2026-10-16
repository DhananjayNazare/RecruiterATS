"""AI ON/OFF buttons, no interviewer name in the candidate's interview e-mail, AI Provider card removed."""
from werkzeug.security import generate_password_hash
from recruiter_ats.database import db, get_setting
from recruiter_ats.services.screening import apply_screening_result
from recruiter_ats.services.scheduling import auto_schedule_interview

def _app(status="Applied"):
    con = db(); now = "2026-10-10T10:00:00"
    con.execute("INSERT INTO jobs(title,description,status,created_at) VALUES('Dev','JD','Open',?)", (now,))
    jid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO candidates(name,email,created_at) VALUES('Neha Joshi','neha@x.test',?)", (now,))
    cid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.execute("INSERT INTO applications(candidate_id,job_id,status,created_at) VALUES(?,?,?,?)", (cid, jid, status, now))
    aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit(); con.close(); return aid

def _status(aid):
    con = db(); r = con.execute("SELECT status FROM applications WHERE id=?", (aid,)).fetchone()[0]; con.close(); return r

def test_settings_has_ai_on_off_not_provider(env, admin):
    page = admin.get("/settings").data
    assert b"AI Provider" not in page and b"AI On / Off" in page and b'id="aiPill"' in page

def test_switch_route_saves_and_validates(env, admin):
    assert admin.post("/ai/switch", key="ai_master", value="0").status_code == 200
    assert get_setting("ai_master", "1") == "0"
    assert admin.post("/ai/switch", key="bogus", value="1").status_code == 400
    assert b"AI OFF" in admin.get("/candidates").data

def test_ai_on_decides_and_books(env):
    aid = _app()
    out = apply_screening_result(aid, 92, {}, 85)
    assert out["status"] == "Interview" and out["interview"]["status"] == "scheduled"

def test_ai_off_recruiter_decides(env):
    from recruiter_ats.database import set_setting
    set_setting("ai_master", "0")
    aid = _app(); out = apply_screening_result(aid, 92, {}, 85)
    assert _status(aid) == "Screening" and out["interview"] is None and out["suggested_status"] == "Interview"

def test_booking_off_only_suggests_interview(env):
    from recruiter_ats.database import set_setting
    set_setting("ai_auto_schedule", "0")
    aid = _app(); out = apply_screening_result(aid, 92, {}, 85)
    assert _status(aid) == "Shortlisted" and out["interview"] is None

def test_candidate_mail_has_no_interviewer_name(env):
    con = db()
    con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES('Secret Interviewer','sec@x.test',?,'Interviewer','2026-01-01')", (generate_password_hash("x"),))
    con.commit(); con.close()
    aid = _app(); res = auto_schedule_interview(aid)
    assert res["status"] == "scheduled"
    con = db()
    cand = con.execute("SELECT message FROM email_schedules WHERE recipient_type='Candidate'").fetchone()[0]
    iv = con.execute("SELECT message FROM email_schedules WHERE recipient_type='Interviewer'").fetchone()[0]; con.close()
    name = res["interviewer"]
    assert name not in cand and "Interviewer" not in cand
    assert name in iv      # the interviewer's own mail still greets them by name


# ---- local Ollama / OpenAI-compatible endpoint ----
class _FakeOpenAI:
    """Stands in for the openai package: an Ollama-like server with one installed model."""
    fail_models = False
    seen = {}
    def __init__(self, api_key=None, base_url=None, timeout=None, max_retries=None):
        _FakeOpenAI.seen = {"api_key": api_key, "base_url": base_url}
        outer = self
        class _M:
            def list(self_):
                if _FakeOpenAI.fail_models: raise ConnectionError("refused")
                class R: data = [type("m", (), {"id": "llama3.1:latest"})()]
                return R()
        class _C:
            def create(self_, **kw):
                _FakeOpenAI.seen["kw"] = kw
                msg = type("x", (), {"content": '<think>hmm</think>```json\n{"score": 77}\n```'})()
                return type("r", (), {"choices": [type("c", (), {"message": msg})()]})()
        self.models = _M(); self.chat = type("ch", (), {"completions": _C()})()


def _local_env(monkeypatch):
    import sys, types
    from recruiter_ats.services import ai
    mod = types.ModuleType("openai"); mod.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setenv("OPENAI_BASE_URL", "localhost:11434/v1"); monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "llama3.1")
    _FakeOpenAI.fail_models = False
    ai._AI_STATUS_CACHE.update(at=0.0, connected=False, error="", fails=0, ok_at=0.0)
    return ai


def test_local_ollama_counts_as_configured_and_parses_json(env, monkeypatch):
    ai = _local_env(monkeypatch)
    assert ai.ai_configured() and ai.ai_base_url() == "http://localhost:11434/v1"
    data, err = ai.ai_json("x")
    assert err is None and data == {"score": 77}                     # <think> block and code fence stripped
    assert _FakeOpenAI.seen["base_url"] == "http://localhost:11434/v1" and _FakeOpenAI.seen["api_key"]


def test_connection_status_is_sticky_and_checks_model(env, monkeypatch):
    ai = _local_env(monkeypatch)
    assert ai.check_ai_connection(force=True)["connected"] is True
    _FakeOpenAI.fail_models = True
    assert ai.check_ai_connection(force=True)["connected"] is True   # one blip does not flip it
    assert ai.check_ai_connection(force=True)["connected"] is False  # two in a row do
    _FakeOpenAI.fail_models = False
    assert ai.check_ai_connection(force=True)["connected"] is True   # and it recovers
    monkeypatch.setenv("OPENAI_MODEL", "mistral")
    ai._AI_STATUS_CACHE.update(fails=5, ok_at=0.0)
    r = ai.check_ai_connection(force=True)
    assert r["connected"] is False and "ollama pull mistral" in r["error"]


def test_url_pasted_into_key_field_is_used_as_base_url(env, monkeypatch):
    from recruiter_ats.services import ai
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False); monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "http://192.168.1.50:11434")
    assert ai.ai_base_url() == "http://192.168.1.50:11434/v1" and ai.ai_key() == "" and ai.ai_configured()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real"); monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434")
    assert ai.ai_key() == "sk-real" and ai.ai_base_url() == "http://localhost:11434/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ollama.example.com/v1/")
    assert ai.ai_base_url() == "https://ollama.example.com/v1"
