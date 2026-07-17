import os
from urllib.parse import urlparse

os.environ["DEBUG"] = "False"

from .local import *  # noqa: E402,F403

import dj_database_url  # noqa: E402


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://querolink_test@127.0.0.1:55432/querolink_test",
)


def _validate_test_database_url(url):
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    database = parsed.path.lstrip("/").lower()
    allowed_hosts = {"127.0.0.1", "localhost", "test-postgres"}
    if hostname not in allowed_hosts:
        raise RuntimeError("TEST_DATABASE_URL deve apontar para um host local de testes.")
    if not database or "test" not in database:
        raise RuntimeError("O nome do banco deve conter 'test'.")


_validate_test_database_url(TEST_DATABASE_URL)

DATABASES = {
    "default": dj_database_url.parse(
        TEST_DATABASE_URL,
        conn_max_age=0,
        ssl_require=False,
    )
}

DATABASES["default"]["TEST"] = {
    "NAME": os.environ.get("TEST_DATABASE_NAME", "test_querolink_fast"),
}

if "test" not in DATABASES["default"]["TEST"]["NAME"].lower():
    raise RuntimeError("TEST_DATABASE_NAME deve conter 'test'.")

CELERY_BROKER_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
WHATSAPP_API_BASE_URL = "http://127.0.0.1:9"
SERVICE_FQDN_WEB = "testserver"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
