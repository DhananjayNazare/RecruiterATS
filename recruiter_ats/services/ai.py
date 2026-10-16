"""OpenAI client, JSON helper and JD / threshold helpers for AI screening."""
import json
import os
import re
import threading
import time
from recruiter_ats.database import get_setting, row_get
from recruiter_ats.services.files import clean_resume_text, summarize_resume_text


_AI_STATUS_CACHE = {"at": 0.0, "connected": False, "error": "AI not checked yet.", "fails": 0, "ok_at": 0.0}
_STATUS_LOCK = threading.Lock()


def _env(*names):
    for n in names:
        v = os.getenv(n, "").strip()
        if v: return v
    return ""

def _looks_like_url(v):
    v = (v or "").strip().lower()
    return v.startswith(("http://", "https://")) or bool(re.match(r"^[\w.\-]+:\d{2,5}(/|$)", v))

def ai_base_url():
    """Custom OpenAI-compatible endpoint, e.g. a local Ollama: http://localhost:11434/v1 (empty = OpenAI cloud).
    A URL pasted into the API-key field by mistake is recognised here too."""
    url = _env("OPENAI_BASE_URL", "OPENAI_API_BASE", "AI_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_HOST")
    if not url:
        for n in ("OPENAI_API_KEY", "AI_API_KEY", "OLLAMA_API_KEY"):
            if _looks_like_url(os.getenv(n, "")): url = os.getenv(n, "").strip(); break
    url = url.rstrip("/")
    if url and not url.startswith(("http://", "https://")): url = "http://" + url
    if url and not re.search(r"/(v\d+|api)(/|$)", url.split("://", 1)[1]):
        url += "/v1"                                   # bare http://host:11434 -> .../v1 (the OpenAI-compatible path)
    return url

def ai_key():
    """The secret key. Values that are really URLs (pasted into the wrong field) are not treated as keys."""
    for n in ("OPENAI_API_KEY", "AI_API_KEY", "OLLAMA_API_KEY"):
        v = os.getenv(n, "").strip()
        if v and not _looks_like_url(v): return v
    return ""

def ai_model():
    return _env("OPENAI_MODEL", "AI_MODEL", "OLLAMA_MODEL") or ("llama3.1" if ai_base_url() else "gpt-5-mini")

def ai_configured():
    """AI is on when there is a key (OpenAI) or a custom base URL (a local Ollama needs no key)."""
    return bool(ai_key() or ai_base_url())

def ai_client(timeout=120.0):
    if not ai_configured(): return None, "AI is not configured: set OPENAI_API_KEY, or OPENAI_BASE_URL for a local Ollama, in .env."
    try:
        from openai import OpenAI
        return OpenAI(api_key=ai_key() or "ollama", base_url=ai_base_url() or None, timeout=timeout, max_retries=2), None
    except Exception as e: return None, f"OpenAI package unavailable: {e}"

def _generate(client, system_msg, prompt, json_mode=False):
    """One model call. Local/custom endpoints use the widely supported chat-completions API; OpenAI cloud uses Responses."""
    model = ai_model()
    reasoning = model.lower().startswith(("gpt-5", "o1", "o3", "o4"))     # these reject the temperature parameter
    if ai_base_url():
        kw = {} if reasoning else {"temperature": 0.1}
        msgs = [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
        try:
            r = client.chat.completions.create(model=model, messages=msgs, **({"response_format": {"type": "json_object"}} if json_mode else {}), **kw)
        except Exception as e:
            if json_mode and "response_format" in str(e).lower():
                r = client.chat.completions.create(model=model, messages=msgs, **kw)
            else: raise
        return (r.choices[0].message.content or "").strip()
    def call(**kw):
        return client.responses.create(model=model, input=[{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}], **kw)
    try:
        r = call() if reasoning else call(temperature=0.1)
    except Exception as e:
        if "temperature" in str(e).lower(): r = call()
        else: raise
    return (r.output_text or "").strip()

def _probe():
    """Cheap reachability check: list models (no tokens spent, fast even when a local model is busy)."""
    client, err = ai_client(timeout=8.0)
    if err: return False, err
    try:
        names = [m.id for m in client.models.list().data]
    except Exception as e:
        return False, f"Cannot reach {ai_base_url() or 'OpenAI'}: {e}"
    want = ai_model()
    if ai_base_url() and names and not any(n == want or n.split(":")[0] == want.split(":")[0] and ":" not in want for n in names):
        return False, f"Connected, but model '{want}' is not installed. Run: ollama pull {want}  (installed: {', '.join(names[:5])})"
    return True, ""

def check_ai_connection(force=False):
    """Connection status shown in the header. It does a quick model-list probe, is cached, and only turns to
    'disconnected' after two failures in a row, so one slow answer from a local model does not flip it."""
    now = time.time()
    with _STATUS_LOCK:
        c = _AI_STATUS_CACHE
        ttl = 30 if c["connected"] else 8
        if not force and now - c["at"] < ttl: return {"connected": c["connected"], "error": c["error"]}
    ok, err = _probe()
    with _STATUS_LOCK:
        c = _AI_STATUS_CACHE
        if ok:
            c.update(connected=True, error="", fails=0, ok_at=now)
        else:
            c["fails"] += 1
            if c["fails"] >= 2 or not c["ok_at"]:
                c.update(connected=False, error=err)
            # one failure after a good check: keep showing connected
        c["at"] = now
        return {"connected": c["connected"], "error": c["error"]}

def ai_json(prompt):
    client, err = ai_client()
    if err: return None, err
    system_msg = "You are a professional recruitment assistant. Return ONLY valid JSON matching the requested schema. Be evidence-based and do not invent candidate facts."
    try:
        raw = _generate(client, system_msg, prompt, json_mode=True)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()      # reasoning models (deepseek, qwen3...) on Ollama
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I).strip()
        if not raw.startswith(("{", "[")):                                            # prose around the JSON: take the outermost object
            i, j = raw.find("{"), raw.rfind("}")
            if i >= 0 and j > i: raw = raw[i:j + 1]
        return json.loads(raw), None
    except Exception as e: return None, str(e)

def ai_threshold_default():
    try:
        return max(1, min(100, int(float(get_setting("ai_auto_threshold", "80")))))
    except (TypeError, ValueError):
        return 80

def read_threshold(value):
    try:
        return max(1, min(100, int(float(value))))
    except (TypeError, ValueError):
        return ai_threshold_default()

def position_jd_text(job):
    """Full JD text for a position: the attached JD file text (if any) + typed description + requirements."""
    jd_text = str(row_get(job, "jd_text", "")).strip()
    desc = str(row_get(job, "description", "")).strip()
    req = str(row_get(job, "requirements", "")).strip()
    parts = []
    if jd_text:
        parts.append(jd_text)
    if desc and desc != jd_text and desc not in jd_text:
        parts.append(desc)
    if req:
        parts.append(req)
    return clean_resume_text("\n".join(parts))

AI_SWITCHES = {
    "ai_master": "AI assistant",
    "ai_decide_status": "AI decides candidate status",
    "ai_auto_schedule": "AI books interview & emails candidate",
}

def ai_task_on(key):
    """True when AI may act on its own for this task. Master OFF turns every AI decision over to the recruiter."""
    from recruiter_ats.database import get_setting
    if get_setting("ai_master", "1") != "1":
        return False
    return key == "ai_master" or get_setting(key, "1") == "1"

def screening_band(score, threshold):
    """Single source of truth for AI score -> (decision label, application status)."""
    if score >= threshold: return "Auto Interview", "Interview"
    if score >= 71: return "Shortlisted", "Shortlisted"
    if score >= 51: return "Accepted", "Accepted"
    if score >= 31: return "Hold", "Hold"
    return "Rejected", "Rejected"

def ai_parse_and_screen(resume_text, job):
    """One AI call: extract the candidate profile AND score the resume against the position's JD."""
    resume_summary = summarize_resume_text(resume_text, max_chars=3500)
    jd = position_jd_text(job)
    prompt = f"""Do two tasks using ONLY the supplied text. Do not invent facts.
TASK 1: extract the candidate profile from the resume.
TASK 2: score the candidate against the job description (0-100, evidence based).
Return ONLY this JSON: {{"name":"","email":"","phone":"","location":"","skills":[],"experience_years":0,"education":[],"certifications":[],"summary":"","strengths":[],"score":0,"matching_keywords":[],"missing_requirements":[],"why_match":[]}}
Rules: experience_years numeric; score integer 0-100; matching_keywords up to 5; missing_requirements up to 5; why_match exactly 3 concise reasons; leave a field empty when the resume does not state it.
POSITION: {row_get(job, 'title')}
JOB DESCRIPTION:
{jd[:16000]}
CANDIDATE RESUME:
{resume_summary}"""
    return ai_json(prompt)

def _to_float(v):
    try: return float(v or 0)
    except (TypeError, ValueError): return 0.0
