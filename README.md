# RecruiterATS AI Professional v2.2

Production patch with automatic backup and Excel-style recruitment reporting.

## Automatic Backup
Open **Settings → Automatic Backup** as an administrator.

- Enable/disable automatic backup
- Daily, weekly, or hourly schedule
- Select backup time for daily/weekly backups
- Retain a configurable number of backup ZIPs
- Choose a local or mounted backup directory
- Create a backup immediately with **Create Backup Now**
- Each backup contains a consistent SQLite database snapshot and the `uploads/` resume files
- Old backups are automatically pruned according to retention

Default backup directory: `backups/`

For production disaster recovery, use a separate disk/network-mounted directory and periodically test restoring a backup.

## Run
Windows: `run_windows.bat`
Linux: `./run_linux.sh`


## UI v2.4
- Hide/unhide sidebar on laptop and desktop.
- Collapsed sidebar state is remembered in the browser.
- Tablet-optimized two-column layout.
- Mobile slide-out sidebar with overlay and menu button.
- Responsive dashboard/cards/forms and touch-friendly controls.

## Role-specific portals
- Candidate accounts are redirected to a Candidate Portal where they can only browse open jobs and apply.
- Interviewer accounts are redirected to an Interviewer Portal where they can see only interviews assigned to them, candidate details, resume access, schedule and meeting links.
- The old combined Candidate & Interviewer Panel has been removed from the Recruitment navigation.
- Admins can link a Candidate user account to a candidate profile while creating the user.

## Batch Resume Processing
Recruitment > Candidates now supports drag-and-drop or multi-select of up to 10 PDF/DOCX/TXT resumes. Each file is saved and processed into a candidate record in one batch.


### v17 AI UI refresh fix
AI resume parsing and AI screening results are now applied to the current page immediately from the AJAX JSON response. Manual browser refresh is no longer required.

## v16 UI/User Management Update
- Global top navbar remains visible while scrolling on desktop and mobile.
- Existing users can be edited from **Users & Roles → Edit**.
- User edit supports name, email, role, linked candidate profile, and optional password reset.
- Admin accounts remain protected from manager edits; users cannot change their own role.


## v17 - Positions, JD attach, auto-screening and auto interview scheduling
- **Jobs are now Positions** everywhere in the UI. Click a position (Positions page) to open a popup where you attach or replace the JD file (PDF/DOCX/TXT, max 2 MB) and edit the JD text. You can also attach the JD when creating the position.
- **AI Screening and Batch Resume Processing** now take a Position. Each resume is parsed, linked to that position, and scored against that position's JD (attached file + typed text + requirements).
- **Automatic workflow** when the score is at or above the threshold: status -> Interview, the interview calendar is checked, the first free slot (working days/hours, interviewer + candidate conflicts, buffer, minimum notice) is booked, the meeting link is created, and the candidate (and interviewer) are emailed through the existing email scheduler. It runs only if the application has no active interview yet, so re-screening never double-books.
- **Batch**: drop up to 10 resumes, pick a Position, and the whole workflow runs in the background with live progress (3 resumes in parallel). Duplicate resumes (same email) update the existing candidate instead of creating a second one.
- **Search by position**: Candidates page has a search box and position filter (searches name, skills, email and position name); the header search also finds candidates by position.
- **Settings -> AI Auto-Screening & Interview Scheduling** (admin): threshold, on/off, working days/hours, interview length, gap, minimum notice, look-ahead, round name, meeting provider, public app URL.
- Positions created later still auto-match against existing candidates, but that background match does **not** book interviews or send emails (use Find Matches or screen resumes for that).
- Set the **Public app URL** in Settings (or `ARS_BASE_URL` in `.env`) so meeting links in emails use an address candidates can reach.

## Running the tests

    pip install -r requirements-dev.txt
    python -m pytest tests

The tests use a temporary database, so your real data is never touched.

## Project structure

    app.py                  development entry point (python app.py)
    wsgi.py                 production entry point (waitress / gunicorn)
    recruiter_ats/
      config.py             Flask app, paths, constants, optional libraries
      database.py           SQLite connection, schema/migrations, settings, super user
      security.py           login helpers, permission decorators, CSRF, central access rules
      services/             business logic (mailer, backup, files, ai, scheduling, screening)
      routes/               web pages and endpoints, one file per area
                            (auth, dashboard, jobs, portals, candidates, ai,
                             interviews, operations, admin, settings)
    templates/  static/     HTML and assets
    tests/                  automated tests

Routes keep their original endpoint names, so `url_for('jobs')` and friends work unchanged.
To add a page: create the function in the matching `routes/` file with `@app.route(...)`.

## Voice assistant (works offline)
Microphone icon in the top bar. It summarises **daily tasks** (today's/tomorrow's interviews, due scheduled emails,
applications awaiting review), **custom tasks** (the To-Do list: overdue, due today, upcoming) and **my reminders**.
- Summaries are built from the local database — no AI model, no internet.
- **My reminders**: type what to remember and pick a date/time in the panel (or say "remind me in 10 minutes to ...").
  They are saved on the server per user, listed in the panel (× deletes one) and fire as a pop-up, beep and spoken alert.
- Speech uses the browser's built-in voices (pick one marked "offline"). Alerts run in the browser from cached data, so they
  continue if the server or AI is down.
- Commands (type, click a button, or use the Mic): "summarise my day", "daily tasks", "custom tasks", "reminders",
  "add task call Raj tomorrow", "remind me in 10 minutes to send the offer", "remind me at 3 pm to ...", "list reminders", "stop".
- Voice *input* uses the browser's recognizer. It needs (1) microphone permission, (2) a secure page — `https://` or
  `http://localhost` — and (3) in Chrome/Edge an internet connection. The assistant now explains which one is missing.
  Buttons and typed commands always work offline.
- Bootstrap/fonts load from a CDN in the existing pages; the assistant itself has no external dependencies.

## Voice commands (locked service)
Next to the always-available assistant above, the panel has a **Voice commands** box. It lets a recruiter or admin open pages,
search, and add / update / delete records by voice. It is locked behind its own password:
- **Voice password** — typed only (never spoken), at least 16 characters with a number, a special symbol, an uppercase and a
  lowercase letter. It must differ from the login password. Creating or changing it needs the login password.
- **Never stays unlocked** — locks after 5 minutes without a command (`ARS_VOICE_IDLE_SECONDS`), on "lock", when the tab is hidden
  for 3 minutes, and whenever the user logs off. The unlock lives only in the server session; nothing is kept in the browser.
- **Wrong password** — 5 failures block the service for 15 minutes (audit-logged). An admin/user manager can clear someone's
  voice password with **Reset voice** on the Admin page; the user then creates a new one.
- **Permissions** — every command re-checks the user's permission on the server: users = *Manage Users*, candidates and AI
  screening = *Manage Candidates*, positions = *Manage Jobs*, interviews = *Manage Interviews*, reports = *View Reports*.
  Without it the assistant refuses (and the refusal is audit-logged). Only an Admin can create an Admin.
- **Confirmation** — deletes, add user, add candidate, schedule/cancel interview, AI screening and Rejected/Hired ask
  "say yes to confirm" and run exactly what was read back.
- **Passwords by voice** — a new user gets a temporary password shown on screen once (never spoken, never logged) and must change it at first login.
- Summarise my day, daily tasks, custom tasks, reminders and "add task" still work with no voice password.
- Commands: "open candidates", "find Priya", "how many applications", "add candidate Priya Sharma email priya at example dot com",
  "add position Python Developer", "add user Rahul Sharma, rahul at example dot com, as recruiter", "move Priya to shortlisted",
  "reject Amit for QA Engineer", "schedule interview for Priya tomorrow at 11 am", "cancel interview for Priya",
  "screen Priya for Python Developer", "mark task call Raj done", "delete candidate / user / position / task ...", "lock".

## HTTPS (SSL certificate)
HTTPS is on by default (`https://localhost:5001`) with an auto-created self-signed certificate in `certs/`, or your own via
`ARS_SSL_CERT` / `ARS_SSL_KEY` in `.env`. Details: `README_PRODUCTION.md`, section 4.

## Meetings (Teams / Google Meet / Zoom)
The built-in ATS video room was removed. When scheduling an interview choose Microsoft Teams, Google Meet, Zoom or Custom
Meeting and paste the invite link (only https:// links are accepted). Change it any time with the pencil button on
Interviews. For AI auto-scheduling, set a provider and an optional default meeting link in Settings. Old interviews that used
the built-in room become "Custom Meeting" with no link, so add a link to them.

## v27 changes
- Jitsi removed everywhere: old Jitsi links/providers become "Custom Meeting" automatically at startup; demo data no longer creates them.
- Candidates page shows 5 profile cards per row (4 / 3 / 2 / 1 on narrower screens).
- The signed-in user and **Log off** moved from the sidebar to the top navbar.
- Dashboard KPI percentages are real: this calendar month vs last month (interviews are counted by scheduled date).
- Voice assistant: microphone start-up and error messages fixed; duplicate "Interviews" button replaced by **Reminders**.

## v28 changes
- New locked **Voice commands** service (separate typed voice password, auto-lock, permission checks, confirmations, audit log).
- Admin page: **Reset voice** button to clear a user's voice password.
- A login password can no longer be set equal to the user's voice password.
