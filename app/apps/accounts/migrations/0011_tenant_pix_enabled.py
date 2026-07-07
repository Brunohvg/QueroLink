from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_tenant_link_expires_in'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='pix_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Incluir PIX como metodo de pagamento nos links',
            ),
        ),
    ]
