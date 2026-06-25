import functools
import hashlib
import base64
import logging

from django.conf import settings
from django.db import models
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _derive_fernet_key():
    key = getattr(settings, 'FERNET_KEY', None)
    if key and isinstance(key, str) and len(key) > 30:
        return key.encode()

    raw = settings.SECRET_KEY.encode()
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


@functools.lru_cache(maxsize=1)
def _get_fernet():
    return Fernet(_derive_fernet_key())


def _reset_fernet_cache():
    _derive_fernet_key.cache_clear()
    _get_fernet.cache_clear()


class EncryptedCharField(models.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_length', 600)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            logger.warning('Failed to decrypt EncryptedCharField value')
            return value

    def to_python(self, value):
        if value is None:
            return value
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return _get_fernet().encrypt(value.encode()).decode()
        return value


class EncryptedTextField(models.TextField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        try:
            return _get_fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            logger.warning('Failed to decrypt EncryptedTextField value')
            return value

    def to_python(self, value):
        if value is None:
            return value
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return _get_fernet().encrypt(value.encode()).decode()
        return value


def compute_hash(value):
    if not value:
        return None
    raw = str(value).strip().lower().encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def scrub_payment_payload(payload):
    if not isinstance(payload, dict):
        return payload
    pii_keys = {
        'customer', 'billing_address', 'shipping_address',
        'card', 'phone', 'document', 'email', 'statement_descriptor',
    }
    cleaned = {}
    for key, value in payload.items():
        if key in pii_keys:
            cleaned[key] = '[REDACTED]'
        elif key == 'last_transaction' and isinstance(value, dict):
            cleaned[key] = scrub_payment_payload(value)
        elif isinstance(value, dict):
            cleaned[key] = scrub_payment_payload(value)
        elif isinstance(value, list):
            cleaned[key] = [
                scrub_payment_payload(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned
