from .base import *

DEBUG = True
ALLOWED_HOSTS = ['*']

# Evolution API para testes locais
WHATSAPP_API_BASE_URL = config('WHATSAPP_API_BASE_URL', default='http://localhost:8080')

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'vcom-local',
    }
}

CORS_ALLOW_ALL_ORIGINS = True
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
