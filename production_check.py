"""Static production smoke checks for RecruiterATS.
Run in the project environment: python production_check.py
"""
from pathlib import Path
import ast, re, sys

ROOT = Path(__file__).resolve().parent
errors = []

# The code now lives in the recruiter_ats/ package; check every module.
sources = sorted((ROOT / "recruiter_ats").rglob("*.py")) + [ROOT / "app.py", ROOT / "wsgi.py"]
for path in sources:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"Python syntax in {path.name}: {exc}")

src = "\n".join(p.read_text(encoding="utf-8") for p in sources)
required = [
    'def csrf_token', 'def csrf_protect', '@app.route("/health")',
    '@app.route("/reports")', 'generate_password_hash', 'check_password_hash',
    'PRAGMA foreign_keys=ON'
]
for item in required:
    if item not in src:
        errors.append(f"Missing security/core feature: {item}")

base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
if 'name="csrf-token"' not in base:
    errors.append("CSRF token meta tag missing")
if 'name="_csrf_token"' not in js:
    errors.append("Automatic CSRF form injection missing")

if errors:
    print("FAILED")
    for e in errors: print(" -", e)
    sys.exit(1)
print("PASSED: syntax, core routes/security hooks, CSRF wiring, password hashing, FK enforcement")
