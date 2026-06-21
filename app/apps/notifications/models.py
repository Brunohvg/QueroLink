import uuid
from django.db import models
from django.template import Template, Context
from app.apps.accounts.models import Tenant
from app.apps.orders.models import Order
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod


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
        SELLER_CREDENTIALS = 'seller_credentials', 'Seller Credentials'
        COMMISSION_PAID = 'commission_paid', 'Commission Paid'

    class Channel(models.TextChoices):
        WHATSAPP = 'whatsapp', 'WhatsApp'
        EMAIL = 'email', 'E-mail'
        INTERNAL = 'internal', 'Internal Notification'

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='templates')
    event_type = models.CharField(max_length=50, choices=EventType.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    title = models.CharField(max_length=255, blank=True, null=True)
    body = models.TextField(help_text="Supports Django template variables: {{vendedor}}, {{usuario}}, {{senha}}, {{periodo}}, {{valor}}, {{cliente}}, {{link}}")
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
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    commission_period = models.ForeignKey(CommissionPeriod, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    event_type = models.CharField(max_length=50, choices=MessageTemplate.EventType.choices, default=MessageTemplate.EventType.LINK_CREATED)
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
            models.Index(fields=['tenant', 'event_type', 'status']),
            models.Index(fields=['order', 'status']),
        ]

    def __str__(self):
        return f"Notification {self.uuid} - {self.event_type} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.tenant_id:
            if self.order_id:
                self.tenant = self.order.tenant
            elif self.seller_id:
                self.tenant = self.seller.tenant
        super().save(*args, **kwargs)


class PasswordResetRequest(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='password_resets')
    pin = models.CharField(max_length=6)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'used']),
            models.Index(fields=['pin', 'expires_at']),
        ]

    def __str__(self):
        return f'Reset for {self.user.username}'
