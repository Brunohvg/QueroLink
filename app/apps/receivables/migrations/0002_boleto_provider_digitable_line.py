from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('receivables', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='boleto',
            name='provider_digitable_line',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
