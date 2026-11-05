"""
FulfillIQ — Development settings.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# SQLite for local development — fast, zero-config.
# Switch to PostgreSQL in prod.py (and for performance benchmarks).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
