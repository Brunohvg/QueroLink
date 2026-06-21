from django.db import migrations


def encrypt_existing_plaintext(apps, schema_editor):
    from django.conf import settings
    from cryptography.fernet import Fernet

    key = getattr(settings, 'FERNET_KEY', None)
    if not key:
        key = settings.SECRET_KEY.encode()[:32].ljust(32, b'0')
    if isinstance(key, str):
        key = key.encode()
    fernet = Fernet(key)

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
