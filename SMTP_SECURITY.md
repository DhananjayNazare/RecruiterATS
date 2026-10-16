# SMTP Security / Admin Access

SMTP / Email Server settings are restricted to users whose role is `Admin`.

- Non-admin users cannot see SMTP host, username, from address, or other SMTP configuration values in the Settings page.
- Non-admin POST attempts to change SMTP settings are rejected.
- The SMTP test-email endpoint is Admin-only.
- All Admin users retain access to configure and test the SMTP server.
- SMTP credentials are never re-displayed to non-admin users.
