import calendar
from datetime import date

from django.db import migrations, models


def backfill_period_ranges(apps, schema_editor):
    CommissionPeriod = apps.get_model('commissions', 'CommissionPeriod')
    for period in CommissionPeriod.objects.all().iterator():
        last_day = calendar.monthrange(period.year, period.month)[1]
        period.start_date = date(period.year, period.month, 1)
        period.end_date = date(period.year, period.month, last_day)
        period.label = period.label or f'{period.month:02d}/{period.year}'
        period.save(update_fields=['start_date', 'end_date', 'label'])


class Migration(migrations.Migration):

    dependencies = [
        ('commissions', '0004_alter_sellercommission_seller'),
    ]

    operations = [
        migrations.AddField(
            model_name='commissionperiod',
            name='label',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='start_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='commissionperiod',
            name='end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_period_ranges, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='commissionperiod',
            name='start_date',
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name='commissionperiod',
            name='end_date',
            field=models.DateField(),
        ),
        migrations.AddIndex(
            model_name='commissionperiod',
            index=models.Index(fields=['tenant', 'start_date', 'end_date'], name='commissions_tenant__fb33df_idx'),
        ),
    ]
