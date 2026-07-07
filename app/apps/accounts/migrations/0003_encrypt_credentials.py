import hashlib
import base64
from django.db import migrations


def _derive_fernet_key():
    from django.conf import settings

    key = getattr(settings, 'FERNET_KEY', None)
    if key and isinstance(key, str) and len(key) > 30:
        return key.encode()

    raw = settings.SECRET_KEY.encode()
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_existing_plaintext(apps, schema_editor):
    from cryptography.fernet import Fernet

    fernet = Fernet(_derive_fernet_key())

    Tenant = apps.get_model('accounts', 'Tenant')
    for tenant in Tenant.objects.all():
        updated = {}
        if tenant.pagarme_api_key and len(tenant.pagarme_api_key) < 200:
            updated['pagarme_api_key'] = fernet.encrypt(tenant.pagarme_api_key.encode()).decode()
        if tenant.whatsapp_token and len(tenant.whatsapp_token) < 200:
            updated['whatsapp_token'] = fernet.encrypt(tenant.whatsapp_token.encode()).decode()
        if updated:
            Tenant.objects.filter(pk=tenant.pk).update(**updated)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_add_user_and_commission_rate'),
    ]

    operations = [
        migrations.RunPython(encrypt_existing_plaintext, reverse_code=noop),
    ]
