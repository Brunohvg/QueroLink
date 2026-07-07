import logging

from django.db import migrations
from django.db.models import Count

logger = logging.getLogger(__name__)


def dedupe_cpf(apps, schema_editor):
    Seller = apps.get_model('sellers', 'Seller')
    dupes = (
        Seller.objects
        .exclude(cpf__isnull=True)
        .exclude(cpf='')
        .values('tenant', 'cpf')
        .annotate(cnt=Count('cpf'))
        .filter(cnt__gt=1)
    )
    dupe_count = dupes.count()
    if dupe_count == 0:
        return

    logger.warning('dedupe_cpf: %d grupos de CPF duplicados encontrados', dupe_count)
    cleared = 0
    for group in dupes:
        sellers = Seller.objects.filter(
            tenant=group['tenant'],
            cpf=group['cpf'],
        ).order_by('created_at', 'pk')

        keep = sellers.first()
        for dup in sellers[1:]:
            logger.warning(
                'dedupe_cpf: removendo CPF duplicado seller pk=%s (mantido em pk=%s)',
                dup.pk, keep.pk,
            )
            dup.cpf = None
            dup.save(update_fields=['cpf'])
            cleared += 1

    logger.info('dedupe_cpf: %d CPFs duplicados limpos', cleared)


class Migration(migrations.Migration):

    dependencies = [
        ('sellers', '0011_normalize_cpf_digits'),
    ]

    operations = [
        migrations.RunPython(dedupe_cpf, migrations.RunPython.noop),
    ]
