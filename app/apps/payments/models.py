import uuid
from django.db import models
from app.apps.orders.models import Order
from app.apps.accounts.fields import scrub_payment_payload

class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'
        CHARGEBACK = 'CHARGEBACK', 'Chargeback'

    class PaymentMethod(models.TextChoices):
        CREDIT_CARD = 'credit_card', 'Credit Card'
        PIX = 'pix', 'Pix'
        BOLETO = 'boleto', 'Boleto'
        UNKNOWN = 'unknown', 'Unknown'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    gateway_name = models.CharField(max_length=50, default='pagarme')
    gateway_transaction_id = models.CharField(max_length=100, unique=True, blank=True, null=True)
    gateway_order_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.UNKNOWN)
    installments = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    raw_callback_payload = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['order', 'status']),
            models.Index(fields=['gateway_transaction_id']),
        ]

    def __str__(self):
        return f"Payment {self.uuid} - {self.status}"

    def save(self, *args, **kwargs):
        if self.raw_callback_payload and isinstance(self.raw_callback_payload, dict):
            self.raw_callback_payload = scrub_payment_payload(self.raw_callback_payload)
        super().save(*args, **kwargs)

    @property
    def refusal_reason(self):
        payload = self.raw_callback_payload or {}
        last_transaction = (payload.get('last_transaction') or {})
        if last_transaction:
            return (last_transaction.get('refuse_reason')
                    or last_transaction.get('acquirer_message')
                    or last_transaction.get('status_reason'))
        return payload.get('refuse_reason') or payload.get('acquirer_message')
