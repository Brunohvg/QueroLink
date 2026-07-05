from django.db import migrations, models
import app.apps.accounts.fields


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_freight_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='correios_cartao',
            field=models.CharField(blank=True, help_text='Cartao de postagem (opcional)', max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='tenant',
            name='correios_codigo_acesso',
            field=app.apps.accounts.fields.EncryptedCharField(blank=True, help_text='Codigo de acesso a API CWS', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='tenant',
            name='correios_contrato',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='tenant',
            name='correios_usuario',
            field=models.CharField(blank=True, help_text='Usuario Meu Correios (idCorreios)', max_length=100, null=True),
        ),
    ]
