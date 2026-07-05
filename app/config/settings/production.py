import dj_database_url
import os
from celery.schedules import crontab
from .base import *

DEBUG = config('DEBUG', default=False, cast=bool)

_hosts = config('ALLOWED_HOSTS', default='')
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(',') if h.strip()] + ['localhost', '127.0.0.1']
_service_fqdn = config('SERVICE_FQDN_WEB', default='')
if _service_fqdn and _service_fqdn not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_service_fqdn)

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=config('CONN_MAX_AGE', default=600, cast=int),
        conn_health_checks=True,
        ssl_require=config('DB_SSL_REQUIRE', default=True, cast=bool),
    )
}

CELERY_BROKER_URL = config('REDIS_URL')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': config('REDIS_URL'),
        'KEY_PREFIX': 'vcom',
    }
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
SECURE_REDIRECT_EXEMPT = [r'^health/$']
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=True, cast=bool)
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=True, cast=bool)
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Security headers explicitos
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = 'same-origin'

# Content Security Policy via middleware customizado
MIDDLEWARE = MIDDLEWARE + ['app.apps.accounts.csp_middleware.CSPMiddleware']

# Celery producao
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 100
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 200000
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_BEAT_SCHEDULE = {
    'daily-db-backup': {
        'task': 'app.apps.accounts.tasks.daily_backup',
        'schedule': crontab(hour=2, minute=0),
    },
    'send-daily-entry-reminders': {
        'task': 'app.apps.notifications.tasks.send_daily_entry_reminders',
        'schedule': 900.0,  # a cada 15 minutos
    },
    'reconcile-pending-orders': {
        'task': 'app.apps.webhooks.tasks.reconcile_pending_orders',
        'schedule': 1800.0,  # a cada 30 minutos
    },
    'cleanup-old-webhook-events': {
        'task': 'app.apps.webhooks.tasks.cleanup_old_webhook_events',
        'schedule': 86400.0,  # diariamente
    },
    'requeue-stuck-notifications': {
        'task': 'app.apps.notifications.tasks.requeue_stuck_notifications',
        'schedule': 600.0,
    },
    'send-lifecycle-emails': {
        'task': 'app.apps.notifications.tasks.send_lifecycle_emails',
        'schedule': crontab(hour=9, minute=0),
    },
}

# E-mail em producao — respeita env var, default SMTP
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': config('LOG_LEVEL', default='WARNING'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': config('LOG_LEVEL', default='WARNING'),
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'app': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ── Sentry (observabilidade de erros) ─────────────────────
SENTRY_DSN = config('SENTRY_DSN', default='')
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        environment=config('SENTRY_ENVIRONMENT', default='production'),
        traces_sample_rate=config('SENTRY_TRACES_SAMPLE_RATE', default=0.1, cast=float),
        send_default_pii=False,
    )
