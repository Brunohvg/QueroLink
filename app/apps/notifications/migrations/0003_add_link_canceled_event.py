# Generated manually — add LINK_CANCELED to MessageTemplate.EventType choices
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_remove_passwordresetrequest_notificatio_pin_a5773f_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='messagetemplate',
            name='event_type',
            field=models.CharField(max_length=50, choices=[
                ('link_created', 'Link Created'),
                ('link_canceled', 'Link Canceled'),
                ('link_opened', 'Link Opened'),
                ('checkout_started', 'Checkout Started'),
                ('payment_paid', 'Payment Paid'),
                ('payment_failed', 'Payment Failed'),
                ('payment_expired', 'Payment Expired'),
                ('payment_refunded', 'Payment Refunded'),
                ('payment_chargeback', 'Payment Chargeback'),
                ('seller_credentials', 'Seller Credentials'),
                ('commission_paid', 'Commission Paid'),
            ]),
        ),
        migrations.AlterField(
            model_name='notification',
            name='event_type',
            field=models.CharField(max_length=50, choices=[
                ('link_created', 'Link Created'),
                ('link_canceled', 'Link Canceled'),
                ('link_opened', 'Link Opened'),
                ('checkout_started', 'Checkout Started'),
                ('payment_paid', 'Payment Paid'),
                ('payment_failed', 'Payment Failed'),
                ('payment_expired', 'Payment Expired'),
                ('payment_refunded', 'Payment Refunded'),
                ('payment_chargeback', 'Payment Chargeback'),
                ('seller_credentials', 'Seller Credentials'),
                ('commission_paid', 'Commission Paid'),
            ], default='link_created'),
        ),
    ]