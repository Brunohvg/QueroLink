from django.db import migrations
from app.apps.accounts.validators import clean_phone


def normalize_phones(apps, schema_editor):
    Seller = apps.get_model('sellers', 'Seller')
    for seller in Seller.objects.iterator():
        cleaned = clean_phone(seller.phone)
        if cleaned != seller.phone:
            seller.phone = cleaned
            seller.save(update_fields=['phone'])


class Migration(migrations.Migration):

    dependencies = [
        ('sellers', '0004_make_user_required'),
    ]

    operations = [
        migrations.RunPython(normalize_phones, reverse_code=migrations.RunPython.noop),
    ]
