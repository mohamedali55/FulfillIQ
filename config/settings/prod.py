"""
FulfillIQ — Production settings.
PostgreSQL, Redis caching, tighter security.
"""

import os

from .base import *  # noqa: F401, F403

DEBUG = False

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# PostgreSQL — required for EXPLAIN ANALYZE, composite indexes, and benchmarks.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "fulfilliq"),
        "USER": os.environ.get("DB_USER", "fulfilliq"),
        "PASSWORD": os.environ.get("FULFILLIQ_DB_PASSWORD", os.environ.get("DB_PASSWORD", "")),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1"),
        "TIMEOUT": 300,
    }
}

CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/2"),
)
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND",
    os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/3"),
)
CELERY_TASK_ALWAYS_EAGER = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
