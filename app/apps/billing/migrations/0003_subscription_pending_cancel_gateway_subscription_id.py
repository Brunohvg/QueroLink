from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_alter_subscription_plan_alter_subscription_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='pending_cancel_gateway_subscription_id',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
    ]
