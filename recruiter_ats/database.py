"""SQLite connection, schema creation/migrations and the app_settings key-value store."""
import os
import secrets
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from recruiter_ats import config


def db():
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")      # safe with WAL, far fewer fsyncs
    con.execute("PRAGMA temp_store=MEMORY")
    return con

def ensure_super_user(con):
    """Create the single hidden backend super user (never shown in the Admin panel).
    Credentials come from ARS_SUPERUSER_EMAIL / ARS_SUPERUSER_PASSWORD. If no password is set,
    a random one is generated and printed ONCE to the server console."""
    if con.execute("SELECT 1 FROM users WHERE is_super=1 LIMIT 1").fetchone():
        return
    email = os.getenv("ARS_SUPERUSER_EMAIL", "superuser@ars.local").strip().lower()
    password = os.getenv("ARS_SUPERUSER_PASSWORD", "").strip()
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)
    if con.execute("SELECT 1 FROM users WHERE lower(email)=?", (email,)).fetchone():
        print("[ARS] Super user not created: email %s already belongs to another account." % email)
        return
    con.execute("INSERT INTO users(name,email,password_hash,role,is_super,created_at) VALUES(?,?,?,?,1,?)",
                ("System Super User", email, generate_password_hash(password), "Admin",
                 datetime.now().isoformat(timespec="seconds")))
    if generated:
        print("[ARS] Hidden super user created -> email: %s  password: %s  (shown once; set ARS_SUPERUSER_PASSWORD to choose your own)" % (email, password))

def init_db():
    con = db()
    try: con.execute("PRAGMA journal_mode=WAL")      # readers no longer block the writer (and vice versa)
    except sqlite3.DatabaseError: pass
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Recruiter', active INTEGER DEFAULT 1,
      candidate_id INTEGER, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,industry TEXT,website TEXT,email TEXT,contact_person TEXT,contact_email TEXT,contact_number TEXT,categories TEXT,locations TEXT,skills TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS skills(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS locations(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,company_id INTEGER,category_id INTEGER,
      location_id INTEGER,description TEXT NOT NULL,requirements TEXT,employment_type TEXT,
      status TEXT DEFAULT 'Open',created_by INTEGER,created_at TEXT,updated_at TEXT,
      FOREIGN KEY(company_id) REFERENCES companies(id), FOREIGN KEY(category_id) REFERENCES categories(id),
      FOREIGN KEY(location_id) REFERENCES locations(id), FOREIGN KEY(created_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS candidates(
      id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT,phone TEXT,location TEXT,
      resume_file TEXT,resume_text TEXT,skills TEXT,experience_years REAL,education TEXT,
      ai_summary TEXT,created_at TEXT,updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS applications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,candidate_id INTEGER NOT NULL,job_id INTEGER NOT NULL,
      status TEXT DEFAULT 'Applied',source TEXT,match_score REAL,ai_screening TEXT,created_at TEXT,updated_at TEXT,
      UNIQUE(candidate_id,job_id),FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
      FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS interviews(
      id INTEGER PRIMARY KEY AUTOINCREMENT,application_id INTEGER NOT NULL,round_name TEXT,
      scheduled_at TEXT,mode TEXT,interviewer TEXT,interviewer_user_id INTEGER,meeting_link TEXT,feedback TEXT,status TEXT DEFAULT 'Scheduled',
      FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE,
      FOREIGN KEY(interviewer_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS todos(
      id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,due_date TEXT,assigned_to INTEGER,
      status TEXT DEFAULT 'Open',created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,action TEXT,entity TEXT,entity_id INTEGER,
      details TEXT,created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS app_settings(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_permissions(
      user_id INTEGER NOT NULL, permission TEXT NOT NULL, granted INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(user_id, permission),
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS email_schedules(
      id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER, recipient_type TEXT NOT NULL,
      recipient_name TEXT, recipient_email TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending', error TEXT,
      created_by INTEGER, created_at TEXT, sent_at TEXT,
      FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE,
      FOREIGN KEY(created_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS job_portals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, website TEXT,
      posting_url TEXT, enabled INTEGER DEFAULT 1, notes TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS job_portal_posts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL, portal_id INTEGER NOT NULL,
      external_job_id TEXT, external_url TEXT, status TEXT DEFAULT 'Draft',
      posted_at TEXT, created_at TEXT, updated_at TEXT,
      UNIQUE(job_id,portal_id),
      FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
      FOREIGN KEY(portal_id) REFERENCES job_portals(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS reminders(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL,
      remind_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS meeting_settings(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS batch_runs(
      id TEXT PRIMARY KEY, job_id INTEGER, status TEXT NOT NULL DEFAULT 'Running', total INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
      created_by INTEGER, created_at TEXT, finished_at TEXT, results TEXT
    );
    """)
    # Lightweight migration for databases created before contact fields / SMTP settings existed.
    existing_cols = {r["name"] for r in con.execute("PRAGMA table_info(companies)").fetchall()}
    if "email" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN email TEXT")
    if "contact_person" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN contact_person TEXT")
    if "contact_email" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN contact_email TEXT")
    if "contact_number" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN contact_number TEXT")
    if "categories" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN categories TEXT")
    if "locations" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN locations TEXT")
    if "skills" not in existing_cols:
        con.execute("ALTER TABLE companies ADD COLUMN skills TEXT")
    existing_user_cols = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "candidate_id" not in existing_user_cols:
        con.execute("ALTER TABLE users ADD COLUMN candidate_id INTEGER")
    existing_job_cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
    if "ai_jd_analysis" not in existing_job_cols:
        con.execute("ALTER TABLE jobs ADD COLUMN ai_jd_analysis TEXT")
    if "ai_jd_questions" not in existing_job_cols:
        con.execute("ALTER TABLE jobs ADD COLUMN ai_jd_questions TEXT")
    # Position JD attachment (file + extracted text) used by AI screening.
    for col in ("jd_file", "jd_original_name", "jd_text", "jd_updated_at"):
        if col not in existing_job_cols:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")

    existing_interview_cols = {r["name"] for r in con.execute("PRAGMA table_info(interviews)").fetchall()}
    if "interviewer_user_id" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN interviewer_user_id INTEGER")
    if "meeting_link" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN meeting_link TEXT")
    if "meeting_provider" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN meeting_provider TEXT")
    if "meeting_room" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN meeting_room TEXT")
    if "meeting_token" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN meeting_token TEXT")
    if "ai_questions" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN ai_questions TEXT")
    if "ai_feedback" not in existing_interview_cols:
        con.execute("ALTER TABLE interviews ADD COLUMN ai_feedback TEXT")
    # Candidate-led AI interview: 48-hour booking window, persisted answers, browser
    # attention signals and a detailed report for recruiters.
    for col in ("ai_answers", "ai_report", "ai_tracking", "available_from", "available_until",
                "ai_started_at", "ai_completed_at"):
        if col not in existing_interview_cols:
            con.execute(f"ALTER TABLE interviews ADD COLUMN {col} TEXT")
    existing_candidate_cols = {r["name"] for r in con.execute("PRAGMA table_info(candidates)").fetchall()}
    if "photo" not in existing_candidate_cols:
        con.execute("ALTER TABLE candidates ADD COLUMN photo TEXT")
    if "certifications" not in existing_candidate_cols:
        con.execute("ALTER TABLE candidates ADD COLUMN certifications TEXT")
    if "ai_strengths" not in existing_candidate_cols:
        con.execute("ALTER TABLE candidates ADD COLUMN ai_strengths TEXT")
    existing_application_cols = {r["name"] for r in con.execute("PRAGMA table_info(applications)").fetchall()}
    if "documents" not in existing_application_cols:
        con.execute("ALTER TABLE applications ADD COLUMN documents TEXT")
    if "ai_auto_processed_at" not in existing_application_cols:
        con.execute("ALTER TABLE applications ADD COLUMN ai_auto_processed_at TEXT")
    # A restart while a batch was running leaves it unfinished; mark it so the UI does not wait forever.
    con.execute("UPDATE batch_runs SET status='Interrupted' WHERE status='Running'")
    existing_user_cols2 = {r["name"] for r in con.execute("PRAGMA table_info(users)").fetchall()}
    if "totp_secret" not in existing_user_cols2:
        con.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
    if "totp_enabled" not in existing_user_cols2:
        con.execute("ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0")
    if "totp_required" not in existing_user_cols2:
        con.execute("ALTER TABLE users ADD COLUMN totp_required INTEGER DEFAULT 0")
    if "must_change_password" not in existing_user_cols2:
        con.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    if "is_super" not in existing_user_cols2:
        con.execute("ALTER TABLE users ADD COLUMN is_super INTEGER DEFAULT 0")
    for col, ddl in (("voice_pw_hash", "TEXT"), ("voice_pw_changed_at", "TEXT"),
                     ("voice_failed", "INTEGER DEFAULT 0"), ("voice_locked_until", "TEXT")):
        if col not in existing_user_cols2:     # separate password for the voice-command service
            con.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    if not con.execute("SELECT 1 FROM users WHERE COALESCE(is_super,0)=0 LIMIT 1").fetchone():
        con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                    ("Administrator", os.getenv("ARS_ADMIN_EMAIL","admin@ars.local"),
                     generate_password_hash(os.getenv("ARS_ADMIN_PASSWORD","Admin@123")),
                     "Admin", datetime.now().isoformat(timespec="seconds")))
    ensure_super_user(con)
    if not con.execute("SELECT 1 FROM companies LIMIT 1").fetchone():
        con.executemany("INSERT INTO companies(name,industry,created_at) VALUES(?,?,?)",
                        [("Demo Technologies","IT Services",datetime.now().isoformat(timespec="seconds")),
                         ("Acme HR Solutions","Staffing",datetime.now().isoformat(timespec="seconds"))])
    if not con.execute("SELECT 1 FROM job_portals LIMIT 1").fetchone():
        now = datetime.now().isoformat(timespec="seconds")
        con.executemany("INSERT OR IGNORE INTO job_portals(name,website,posting_url,enabled,notes,created_at) VALUES(?,?,?,?,?,?)", [
            ("LinkedIn","https://www.linkedin.com","https://www.linkedin.com/talent/","1","Use your company LinkedIn/Talent account to publish jobs.",now),
            ("Indeed","https://www.indeed.com","https://employers.indeed.com/","1","Use your Indeed employer account for job posting.",now),
            ("Naukri","https://www.naukri.com","https://recruit.naukri.com/","1","Use your Naukri Recruiter account for job posting.",now),
            ("Foundit","https://www.foundit.in","https://recruiter.foundit.in/","1","Use your Foundit recruiter account for job posting.",now),
            ("Shine","https://www.shine.com","https://recruiter.shine.com/","1","Use your Shine recruiter account for job posting.",now)
        ])
    if not con.execute("SELECT 1 FROM meeting_settings WHERE key='default_provider'").fetchone():
        con.execute("INSERT OR REPLACE INTO meeting_settings(key,value) VALUES('default_provider','Microsoft Teams')")
    # The built-in ATS (Jitsi) meeting was removed: legacy interviews lose that dead link and become "Custom Meeting"
    # (add a Teams / Google Meet / Zoom link with the pencil button on the Interviews page).
    con.execute("UPDATE interviews SET meeting_provider='Custom Meeting', meeting_link='' WHERE LOWER(COALESCE(meeting_provider,'')) LIKE 'recruiter ars%'")
    # Jitsi links (old demo data) are removed too: the interview keeps its slot, only the dead Jitsi link/provider is cleared.
    con.execute("UPDATE interviews SET meeting_provider='Custom Meeting', meeting_link='' "
                "WHERE LOWER(COALESCE(meeting_provider,'')) LIKE '%jitsi%' OR LOWER(COALESCE(meeting_link,'')) LIKE '%jit.si%'")
    con.execute("UPDATE meeting_settings SET value='Microsoft Teams' WHERE key='default_provider' AND (LOWER(value) LIKE 'recruiter ars%' OR LOWER(value) LIKE '%jitsi%')")
    con.execute("UPDATE app_settings SET value='Microsoft Teams' WHERE key='sched_provider' AND (LOWER(value) LIKE 'recruiter ars%' OR LOWER(value) LIKE '%jitsi%')")
    # Indexes for the joins/filters the pages run on every load (CREATE IF NOT EXISTS: safe on old databases).
    for ddl in (
        "applications(candidate_id)", "applications(job_id)", "applications(status)",
        "interviews(application_id)", "interviews(scheduled_at)", "interviews(interviewer_user_id)", "interviews(status)",
        "candidates(email)", "candidates(name)", "jobs(status)", "jobs(company_id)",
        "todos(assigned_to,status)", "audit_log(created_at)", "audit_log(user_id)",
        "reminders(user_id,status,remind_at)", "email_schedules(status,scheduled_at)",
    ):
        name = "idx_" + ddl.replace("(", "_").replace(")", "").replace(",", "_")
        con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {ddl}")
    con.commit(); con.close()

def _settings_cache():
    """All settings, loaded once per request (pages ask for 3-10 settings; this makes it one query)."""
    try:
        from flask import g, has_request_context
        if not has_request_context(): return None
    except RuntimeError:
        return None
    cache = g.get("_settings")
    if cache is None:
        con = db()
        cache = g._settings = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM app_settings")}
        con.close()
    return cache

def get_setting(key, default=""):
    cache = _settings_cache()
    if cache is not None:
        v = cache.get(key)
        return default if v is None else v
    con = db()
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else default

def set_setting(key, value):
    con = db()
    con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit(); con.close()
    try:
        from flask import g, has_request_context
        if has_request_context(): g.pop("_settings", None)
    except RuntimeError:
        pass

def row_get(row, key, default=""):
    """Safe getter for sqlite3.Row / dict values."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return default if v is None else v
