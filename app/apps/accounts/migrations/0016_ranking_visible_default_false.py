# Generated manually — LOTE 2 Privacidade do Ranking

from django.db import migrations, models


def set_ranking_visible_to_false(apps, schema_editor):
    Tenant = apps.get_model('accounts', 'Tenant')
    Tenant.objects.all().update(ranking_visible_to_sellers=False)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_tenant_billing_email'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tenant',
            name='ranking_visible_to_sellers',
            field=models.BooleanField(
                default=False,
                help_text='Exibe nomes dos colegas no ranking do app (valores nunca sao exibidos)',
            ),
        ),
        migrations.RunPython(
            set_ranking_visible_to_false,
            reverse_code=noop,
        ),
    ]
