import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def normalize_cpf_digits(apps, schema_editor):
    Seller = apps.get_model('sellers', 'Seller')
    for seller in Seller.objects.exclude(cpf__isnull=True).exclude(cpf=''):
        digits = ''.join(filter(str.isdigit, seller.cpf))
        if len(digits) == 11:
            seller.cpf = digits
            seller.save(update_fields=['cpf'])
        else:
            logger.warning(
                'normalize_cpf_digits: cpf invalido para seller %s (%s), setando NULL',
                seller.pk, seller.cpf,
            )
            seller.cpf = None
            seller.save(update_fields=['cpf'])


class Migration(migrations.Migration):

    dependencies = [
        ('sellers', '0009_seller_cpf'),
    ]

    operations = [
        migrations.RunPython(normalize_cpf_digits, migrations.RunPython.noop),
    ]
