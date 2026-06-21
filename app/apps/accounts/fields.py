import hashlib
import base64
from django.conf import settings
from django.db import models
from cryptography.fernet import Fernet


def _derive_fernet_key():
    key = getattr(settings, 'FERNET_KEY', None)
    if key and isinstance(key, str) and len(key) > 30:
        return key.encode()

    raw = settings.SECRET_KEY.encode()
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def _get_fernet():
    return Fernet(_derive_fernet_key())


class EncryptedCharField(models.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('max_length', 255)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return _get_fernet().decrypt(value.encode()).decode()

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str) and len(value) > 50:
            return value
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return _get_fernet().encrypt(value.encode()).decode()
        return value
