import uuid
from django.db import models
from django.template import Template, Context
from app.apps.accounts.models import Tenant
from app.apps.orders.models import Order

class MessageTemplate(models.Model):
    class EventType(models.TextChoices):
        LINK_CREATED = 'link_created', 'Link Created'
        LINK_OPENED = 'link_opened', 'Link Opened'
        CHECKOUT_STARTED = 'checkout_started', 'Checkout Started'
        PAYMENT_PAID = 'payment_paid', 'Payment Paid'
        PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
        PAYMENT_EXPIRED = 'payment_expired', 'Payment Expired'
        PAYMENT_REFUNDED = 'payment_refunded', 'Payment Refunded'
        PAYMENT_CHARGEBACK = 'payment_chargeback', 'Payment Chargeback'

    class Channel(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        EMAIL = 'email', 'E-mail'
        INTERNAL = 'internal', 'Internal Notification'

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='templates')
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    title = models.CharField(max_length=255, blank=True, null=True)
    body = models.TextField(help_text="Supports Jinja variables: {{cliente}}, {{valor}}, {{link}}, {{vendedor}}")
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'event_type']),
        ]

    def __str__(self):
        return f"Template {self.event_type} - {self.channel}"

    def render_body(self, context_dict):
        template = Template(self.body)
        context = Context(context_dict)
        return template.render(context)


class Notification(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=20, choices=MessageTemplate.Channel.choices)
    recipient = models.CharField(max_length=255)
    message_body = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    error_log = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['order', 'status']),
        ]

    def __str__(self):
        return f"Notification {self.uuid} - {self.status}"
