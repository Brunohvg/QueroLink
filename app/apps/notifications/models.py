import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.template import Template, Context, TemplateSyntaxError
from app.apps.accounts.models import Tenant
from app.apps.accounts.fields import EncryptedCharField, EncryptedTextField
from app.apps.orders.models import Order
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod

SAMPLE_CONTEXT = {
    'vendedor': 'Fulano',
    'usuario': 'fulano',
    'senha': '********',
    'periodo': '06/2026',
    'valor': 'R$ 1.234,56',
    'cliente': 'Cliente Exemplo',
    'link': 'https://exemplo.com/pagar/abc',
    'motivo': 'Cartao recusado',
}

EVENT_VARIABLES = {
    'seller_credentials': ['vendedor', 'usuario', 'senha'],
    'commission_paid': ['vendedor', 'periodo', 'valor'],
    'commission_adjusted': ['vendedor', 'periodo', 'valor'],
    'link_created': ['vendedor', 'cliente', 'valor', 'link'],
    'payment_paid': ['vendedor', 'cliente', 'valor', 'link'],
    'link_canceled': ['vendedor', 'cliente', 'valor', 'link'],
    'payment_expired': ['vendedor', 'cliente', 'valor', 'link'],
    'payment_failed': ['vendedor', 'cliente', 'valor', 'link', 'motivo'],
    'payment_refunded': ['vendedor', 'cliente', 'valor', 'link'],
    'payment_chargeback': ['vendedor', 'cliente', 'valor', 'link', 'motivo'],
    'link_opened': ['vendedor', 'cliente', 'valor', 'link'],
    'checkout_started': ['vendedor', 'cliente', 'valor', 'link'],
    'daily_reminder': ['vendedor'],
}

EVENT_LABELS = {
    'seller_credentials': 'Credenciais do vendedor',
    'commission_paid': 'Comissao paga',
    'commission_adjusted': 'Comissao ajustada',
    'link_created': 'Link criado',
    'payment_paid': 'Link pago',
    'link_canceled': 'Link cancelado',
    'payment_expired': 'Link expirado',
    'payment_failed': 'Pagamento falhou',
    'payment_refunded': 'Pagamento estornado',
    'payment_chargeback': 'Chargeback',
    'link_opened': 'Link aberto',
    'checkout_started': 'Checkout iniciado',
    'daily_reminder': 'Lembrete diario',
}


class MessageTemplate(models.Model):
    class EventType(models.TextChoices):
        LINK_CREATED = 'link_created', 'Link Created'
        LINK_CANCELED = 'link_canceled', 'Link Canceled'
        LINK_OPENED = 'link_opened', 'Link Opened'
        CHECKOUT_STARTED = 'checkout_started', 'Checkout Started'
        PAYMENT_PAID = 'payment_paid', 'Payment Paid'
        PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
        PAYMENT_EXPIRED = 'payment_expired', 'Payment Expired'
        PAYMENT_REFUNDED = 'payment_refunded', 'Payment Refunded'
        PAYMENT_CHARGEBACK = 'payment_chargeback', 'Payment Chargeback'
        SELLER_CREDENTIALS = 'seller_credentials', 'Seller Credentials'
        COMMISSION_PAID = 'commission_paid', 'Commission Paid'
        COMMISSION_ADJUSTED = 'commission_adjusted', 'Commission Adjusted'
        DAILY_REMINDER = 'daily_reminder', 'Daily Reminder'

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

    def clean(self):
        if not self.body:
            return
        if '{%' in self.body:
            raise ValidationError(
                "Apenas variaveis {{...}} sao permitidas; tags {%%} nao sao suportadas."
            )
        try:
            template = Template(self.body)
            template.render(Context(SAMPLE_CONTEXT))
        except TemplateSyntaxError as e:
            raise ValidationError(f"Template invalido: {e}")


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
    recipient = EncryptedCharField(max_length=600)
    message_body = EncryptedTextField()
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
            elif self.commission_period_id:
                self.tenant = self.commission_period.tenant
        super().save(*args, **kwargs)


class PushSubscription(models.Model):
    user = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='push_subscriptions',
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.TextField()
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'endpoint'], name='unique_user_endpoint'),
        ]

    def __str__(self):
        return f'Push {self.user.username} ({self.endpoint[:40]}...)'


class LifecycleEmail(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='lifecycle_emails')
    trigger = models.CharField(max_length=30)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'trigger'], name='unique_tenant_trigger'),
        ]


class PasswordResetRequest(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='password_resets')
    pin_hash = models.CharField(max_length=128, default='')
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'used']),
            models.Index(fields=['expires_at']),
        ]

    def set_pin(self, raw_pin):
        from django.contrib.auth.hashers import make_password
        self.pin_hash = make_password(raw_pin)

    def check_pin(self, raw_pin):
        from django.contrib.auth.hashers import check_password
        return check_password(raw_pin, self.pin_hash)

    def __str__(self):
        return f'Reset for {self.user.username}'
