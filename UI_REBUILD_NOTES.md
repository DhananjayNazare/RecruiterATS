# RecruiterATS UI Rebuild V2

## Design direction
The UI is now aligned to the approved RecruiterATS template: navy application shell, blue primary actions, white cards, compact tables, recruiter-focused status pills, calendar/interview styling, and consistent responsive behavior.

## Primary screens covered
1. Login / authentication
2. Dashboard
3. Jobs
4. Candidates
5. Candidate Profile
6. AI Screening
7. Interviews / Calendar
8. Interview Details / Create Interview
9. Reschedule / calendar interaction styling
10. Team / Operations
11. Meetings / communication areas
12. Reports

## Supporting/sub-pages covered
- Add/Edit Candidate
- Candidate portal
- Interviewer portal
- Interviewer interview details
- Candidate-interviewer panel
- Meeting room
- Integrations
- Admin / Users & Roles
- User Edit
- Settings / 2FA / SMTP / Backup
- Company Edit
- Applications
- Signup
- Forgot Password
- Force Password Change
- 2FA verification and setup

## Implementation
- Existing application routes, database logic, AI logic, email logic, meeting logic and authentication were preserved.
- A single UI template layer was added at the end of `static/app.css` so the redesign consistently overrides older visual rules without changing business logic.
- `templates/base.html` now provides endpoint-aware page titles/subtitles and a user-initial avatar.
- All 27 Jinja templates were parsed successfully.
- Python syntax checks passed for `app.py` and `wsgi.py`.

## Important mapping
The current application does not have a standalone Messages backend/page. Existing communication functionality remains under Meetings / Operations / Email Scheduler rather than inventing a non-functional messages module.


## V3 update — shell rebuilt to match the template screens
- `templates/base.html`: new layout — full-height navy sidebar (brand, nav, user card) + white top bar (search, notifications, user) + page title inside the content area. JS hooks/ids unchanged.
- `templates/login.html`: split-screen login (navy hero + "Welcome Back" form). `forgot_password.html` uses the envelope reset card.
- `static/ui_template.css` (new, loaded after `app.css`): design tokens, shell, cards, KPI tiles, tables, status pills, calendar, auth screens, responsive/collapsed sidebar.
- `static/app.js`: status pills are colour-coded by their text; the page heading is no longer overwritten by JS.
- Removed the unused `ui_template_v2.css`. No Python/backend changes.

## v29 changes
- Mic: a separate mic button in the top bar (and the mic inside the assistant panel) now turns the mic ON and keeps it on.
  It turns off when you click it again (or say "stop listening"), after 5 minutes with no spoken command, or when you log off.
  While on, it only reacts to real commands (ambient talk is ignored). It stays on when you move between pages in the same tab.
  The assistant panel button now has a robot icon so it is not confused with the mic.
- Notifications: "Clear all" button on the panel, an x on each item, and the unread count shows on page load. Cleared items stay cleared per user.

## v30 changes (two-way voice)
- While the mic is on, the assistant now answers every sentence out loud (greetings, thanks, time, date, help, repeat, plus all earlier commands).
- Turn-taking: the mic pauses while the assistant speaks and listens again when it finishes, so it never hears itself.
- A small live card (top right) shows "You: ..." and "Assistant: ..." with a Turn mic off button, even when the panel is closed.
- Say "goodbye" or "stop listening" to turn the mic off.
