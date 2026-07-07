from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0019_accountant_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='freight_adjustment_percent',
            field=models.IntegerField(default=0, help_text='Calibracao da estimativa em % (-50 a +100)'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='freight_presets',
            field=models.JSONField(blank=True, default=list, help_text='Embalagens frequentes: [{"name": "Caixa P", "weight_grams": 500}]'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='motoboy_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='tenant',
            name='motoboy_max_km',
            field=models.PositiveIntegerField(default=15, help_text='Raio maximo de entrega (km)'),
        ),
        migrations.AddField(
            model_name='tenant',
            name='motoboy_min_price_cents',
            field=models.PositiveIntegerField(default=800),
        ),
        migrations.AddField(
            model_name='tenant',
            name='motoboy_price_per_km_cents',
            field=models.PositiveIntegerField(default=200),
        ),
        migrations.AddField(
            model_name='tenant',
            name='store_cep',
            field=models.CharField(blank=True, help_text='CEP de origem dos envios (loja)', max_length=9, null=True),
        ),
    ]
