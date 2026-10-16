# RecruiterATS v39 — Candidate AI Interview Workflow

## What changed

- Candidate and interviewer portal pages now share the app's Rust UI visual system and responsive card/layout styles.
- Candidate accounts can browse every job with status `Open`, even before the account is linked to a row in the recruiter candidate database.
- Applying to a job creates and links a candidate record when one does not exist, and stores the submitted resume and optional supporting documents.
- Recruiter status changes to `Accepted`, `Shortlisted`, or `Interview` create a 48-hour AI first-round invitation. This applies to the application status screen and the recruiter's voice assistant. A non-completed invitation that expires before the candidate starts can be reopened by a subsequent qualifying recruiter status action; completed reports are preserved.
- Candidates select a time within that 48-hour window. The AI interview page opens 15 minutes before the selected time and is a separate page from external meeting links.
- The AI prepares a 40-question role/resume-specific interview, shown in a paper-like, one-question-at-a-time view. Candidates can type answers or use supported browser speech-to-text. Answers auto-save to the app.
- On submission, the AI creates a detailed report with an overall score, recommendation, evidence-based competency assessment, strengths, development areas, question-by-question analysis, skill evidence, unanswered questions, and suggested next-round questions. Recruiters open the report from **Applications → AI Report**.
- Browser camera tracking is optional after the candidate acknowledges the notice. If it runs, the browser computes approximate face-not-detected, gaze/look-away and head-movement counts. No video is uploaded or stored, and these counts must not be used as evidence of honesty, personality or job competence or as part of the AI score. Camera tracking failures do not block the interview.
- The existing interviewer AI question planner is on its own page, separate from candidate details and meeting links.

## Deployment notes

1. Back up `recruiter_ars.db` and the `uploads/` folder before replacing the current application.
2. Deploy the updated project and install dependencies from the existing `requirements.txt` inside the app's Python virtual environment.
3. Database migrations for the additional interview columns run through the existing `init_db()` startup path. Keep the first startup on a backed-up database.
4. Configure the AI provider using the app's existing `.env` settings (`OPENAI_API_KEY` and/or `OPENAI_BASE_URL`, plus the configured model as needed). Do not enter API secrets in templates or source code.
5. Camera access requires HTTPS or localhost and browser permission. The Face Landmarker library/model are fetched from their configured CDNs when the candidate enables tracking, so browser access to those hosts is needed. Voice-to-text depends on browser support; candidates can type if it is unavailable.
6. A candidate account must have the `Candidate` role to use the portal. Once they apply, their profile is linked to the candidate database and their recruiter status/interview updates appear in that account.

## Validation performed for this revision

- Python source syntax parsed successfully for all 27 application modules.
- Jinja syntax parsed successfully for all 32 templates.
- JavaScript syntax checks passed for the embedded scripts in the changed interview/portal templates.
- The invitation helper was tested in isolation for initial creation, duplicate prevention, expired-window reopening, and completed-report preservation.
- The full pytest suite could not run in the build environment because Flask and Werkzeug are not installed there and package downloads were unavailable. Run the suite in the normal project virtual environment before production deployment.


## v39.1 portal navigation and AI connection status
- Candidate and interviewer panels now share a responsive Rust UI navbar with portal-specific navigation links and log out.
- Both panels show a live AI interview service connectivity badge. A safe, authenticated endpoint exposes only configured/connected state and a user-facing message; it never returns API keys, base URLs, or raw provider errors.
- Candidate navbar links jump to Open Jobs, My Interviews, and My Applications. Interviewer navbar links jump to My Interviews.


## v39.2 candidate AI interview action fix
- Candidate Portal now repairs missing AI Round 1 invitations for existing applications in Accepted, Shortlisted, or Interview status. This covers applications selected before the candidate-led AI workflow was installed and AI-screening paths that changed status without creating the invitation. Existing active/completed AI invites are not duplicated.
- The candidate now sees an explicit **Set Interview Time** submit button during the valid 48-hour booking window. The date/time minimum rounds up to the next minute to avoid browser/server precision disagreements.
- Scheduled interviews show **Take AI Interview** when the room is available (15 minutes before the selected time). Before that, a disabled button states when the room opens; expired windows are clearly explained. In-progress sessions show a continue action.
- Added regression tests for invite backfill, scheduling, and the candidate action labels. Full tests still require the project dependencies installed (Flask/Werkzeug were not present in the build environment).
