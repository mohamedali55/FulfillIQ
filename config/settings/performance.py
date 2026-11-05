"""PostgreSQL settings used for performance tests and query-plan inspection."""

import os

from .dev import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "fulfilliq"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get(
            "FULFILLIQ_DB_PASSWORD",
            os.environ.get("DB_PASSWORD", ""),
        ),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "OPTIONS": {"connect_timeout": 5},
    }
}
