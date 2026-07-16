from django.db.models.signals import post_save
from django.dispatch import receiver

from app.apps.orders.models import Order
from app.apps.receivables.models import Boleto

from .models import CustomerActivity


@receiver(post_save, sender=Boleto)
def update_boleto_activity(sender, instance, **kwargs):
    CustomerActivity.objects.filter(
        source=CustomerActivity.Source.BOLETO,
        source_uuid=instance.uuid,
        customer__tenant=instance.tenant,
    ).update(status=instance.status, amount_cents=instance.amount_cents)


@receiver(post_save, sender=Order)
def update_order_activity(sender, instance, **kwargs):
    CustomerActivity.objects.filter(
        source=CustomerActivity.Source.PAYMENT_LINK,
        source_uuid=instance.uuid,
        customer__tenant=instance.tenant,
    ).update(status=instance.status, amount_cents=instance.total_amount)
