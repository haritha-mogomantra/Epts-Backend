# epts_backend/settings_local.py
from .settings import *   # import everything from base settings

# Use SQLite locally
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# For local dev: relax some production-only settings if needed
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
}
