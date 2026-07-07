from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.utils import OperationalError


def _check_database():
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except OperationalError:
        return False
    return True


def _check_redis():
    cache_backend = cache.__class__.__module__
    if 'redis' not in cache_backend.lower():
        return 'skipped'

    try:
        cache.set('healthcheck:redis', 'ok', timeout=5)
        return 'ok' if cache.get('healthcheck:redis') == 'ok' else 'error'
    except Exception:
        return 'error'


def _check_celery_broker():
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return 'eager'

    try:
        from app.config.celery import app as celery_app
        with celery_app.connection_for_read() as connection:
            connection.ensure_connection(max_retries=1)
    except Exception:
        return 'error'
    return 'ok'


def get_health_status():
    db_ok = _check_database()
    redis_status = _check_redis()
    celery_status = _check_celery_broker()

    degraded = (
        not db_ok
        or redis_status == 'error'
        or celery_status == 'error'
    )

    return {
        'payload': {
            'status': 'degraded' if degraded else 'ok',
            'database': 'ok' if db_ok else 'error',
            'redis': redis_status,
            'celery': celery_status,
        },
        'status_code': 503 if degraded else 200,
    }
