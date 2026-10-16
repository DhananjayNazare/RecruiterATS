# Recruiter ATS — Production Setup

## 1. Job category
The Create Job form now allows users with `manage_jobs` permission to either select an existing category or manually type a new category. New categories are automatically saved to the category master table and reused on future jobs.

## 2. WSGI production server
The project includes `wsgi.py`.

### Windows
Run `setup_production_windows.bat`, set `ARS_SECRET_KEY` in `.env`, then run `run_production_windows.bat`.

### Linux
Run `./setup_production_linux.sh`, set `ARS_SECRET_KEY` in `.env`, then run `./run_production_linux.sh`.

Waitress is used on Windows. Gunicorn is used on Linux. The production setup intentionally uses one WSGI worker because the application currently contains built-in background backup/email worker threads; multiple WSGI workers would duplicate those jobs.

## 3. Production security checklist
- Set a strong unique `ARS_SECRET_KEY` in `.env`.
- Keep `ARS_ENV=production`.
- Do not commit `.env` or API/SMTP credentials.
- HTTPS is on by default (see section 4). For internet deployment use a certificate from a trusted CA.
- Restrict the database/uploads/backups filesystem permissions.
- Configure SMTP and `OPENAI_API_KEY` only when required.
- Back up `recruiter_ars.db`, `uploads/`, and `backups/`.
- Use the `/health` endpoint for service monitoring.

## 4. HTTPS / SSL certificate
HTTPS is enabled by default: `python app.py` (development) and `python serve_https.py` (production, used by
`run_production_windows.bat` / `run_production_linux.sh`) serve `https://localhost:5001`.

- **Default (no setup):** a self-signed certificate is created in `certs/` on first start, valid for localhost, the machine
  name and this machine's IPs, and renewed automatically 30 days before expiry. Browsers show a one-time warning; to remove it,
  import `certs/ats-cert.pem` into the OS/browser trusted certificates on each PC.
- **Trusted certificate (Let's Encrypt, company CA, mkcert):** set both paths in `.env`:
  `ARS_SSL_CERT=/path/fullchain.pem` and `ARS_SSL_KEY=/path/privkey.pem`.
- **Extra names/IPs for the self-signed certificate:** `ARS_SSL_HOSTS=ats.mycompany.local,192.168.1.20`
  (delete `certs/` or change the list to regenerate).
- **Plain HTTP:** set `ARS_SSL=0` and `ARS_SESSION_COOKIE_SECURE=0` in `.env`.
- With HTTPS on, session cookies are marked Secure. Keep `certs/` private (it holds the private key); do not share or commit it.
- HTTPS also lets browsers allow microphone/voice input for the voice assistant when ATS is opened from another PC on the network.
