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
        migrations.AddIndex(
            model_name='subscription',
            index=models.Index(fields=['pending_cancel_gateway_subscription_id'], name='billing_sub_pending_6d2b7c_idx'),
        ),
    ]
