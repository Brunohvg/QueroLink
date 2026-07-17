from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_tenant_period_start_day'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='receivables_enabled',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Habilita recebiveis para este tenant quando o plano permitir'
                ),
            ),
        ),
    ]
