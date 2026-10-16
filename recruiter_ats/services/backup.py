"""Database + upload backups and the background backup worker."""
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from recruiter_ats import config
from recruiter_ats.database import get_setting, set_setting
from recruiter_ats.security import audit


def create_backup():
    """Create a consistent SQLite backup plus uploaded resumes in one ZIP."""
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = config.BACKUP_DIR / f"recruiter_ars_backup_{stamp}.zip"
    temp_db = config.BACKUP_DIR / f".recruiter_ars_{stamp}.db"
    try:
        source = sqlite3.connect(config.DB_PATH)
        target = sqlite3.connect(temp_db)
        source.backup(target)
        target.close(); source.close()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(temp_db, arcname="recruiter_ars.db")
            if config.UPLOAD_DIR.exists():
                for f in config.UPLOAD_DIR.rglob("*"):
                    if f.is_file():
                        z.write(f, arcname=str(Path("uploads") / f.relative_to(config.UPLOAD_DIR)))
        temp_db.unlink(missing_ok=True)
        set_setting("backup_last", datetime.now().isoformat(timespec="seconds"))
        audit("BACKUP","SYSTEM",details=f"Created {zip_path.name}")
        return zip_path, None
    except Exception as e:
        try: temp_db.unlink(missing_ok=True)
        except Exception: pass
        try: zip_path.unlink(missing_ok=True)
        except Exception: pass
        return None, str(e)

def prune_backups():
    try:
        retention = max(1, int(get_setting("backup_retention", "30")))
    except ValueError:
        retention = 30
    files = sorted(config.BACKUP_DIR.glob("recruiter_ars_backup_*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)
    for old in files[retention:]:
        try: old.unlink()
        except OSError: pass

def backup_worker():
    while True:
        try:
            enabled = get_setting("backup_enabled", "0") == "1"
            if enabled:
                freq = get_setting("backup_frequency", "Daily")
                target = get_setting("backup_time", "02:00")
                now = datetime.now()
                try:
                    hh, mm = [int(x) for x in target.split(":", 1)]
                    due_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                except Exception:
                    due_time = now.replace(hour=2, minute=0, second=0, microsecond=0)
                last_raw = get_setting("backup_last", "")
                try: last = datetime.fromisoformat(last_raw) if last_raw else None
                except Exception: last = None
                due = False
                if freq == "Hourly":
                    due = last is None or (now - last) >= timedelta(hours=1)
                elif freq == "Weekly":
                    due = now.weekday() == 6 and now >= due_time and (last is None or last.date() < now.date())
                else:
                    due = now >= due_time and (last is None or last.date() < now.date())
                if due:
                    path, err = create_backup()
                    if path:
                        prune_backups()
                    else:
                        set_setting("backup_last_error", err or "Unknown backup error")
        except Exception:
            pass
        time.sleep(30)
