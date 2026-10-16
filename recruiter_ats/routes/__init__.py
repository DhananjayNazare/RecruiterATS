"""HTTP routes. Importing a module registers its routes on the shared Flask app."""
from recruiter_ats.routes import auth  # noqa: F401
from recruiter_ats.routes import dashboard  # noqa: F401
from recruiter_ats.routes import jobs  # noqa: F401
from recruiter_ats.routes import portals  # noqa: F401
from recruiter_ats.routes import candidates  # noqa: F401
from recruiter_ats.routes import ai  # noqa: F401
from recruiter_ats.routes import interviews  # noqa: F401
from recruiter_ats.routes import operations  # noqa: F401
from recruiter_ats.routes import admin  # noqa: F401
from recruiter_ats.routes import settings  # noqa: F401
from recruiter_ats.routes import assistant  # noqa: F401
from recruiter_ats.routes import voice_service  # noqa: F401
