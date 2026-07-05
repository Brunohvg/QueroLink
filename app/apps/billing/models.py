import uuid
from django.db import models
from django.conf import settings
from app.apps.accounts.models import Tenant


def plan_amount(plan, billing_cycle):
    prices = getattr(settings, 'PLAN_PRICES', {})
    monthly = prices.get(plan, 0)
    if billing_cycle == 'YEARLY':
        return int(monthly * 10)
    return monthly


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        TRIALING = 'TRIALING', 'Periodo de teste'
        ACTIVE = 'ACTIVE', 'Ativa'
        PAST_DUE = 'PAST_DUE', 'Pagamento pendente'
        CANCELED = 'CANCELED', 'Cancelada'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        Tenant, on_delete=models.CASCADE, related_name='subscription',
    )
    plan = models.CharField(max_length=20, choices=Tenant.Plan.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.TRIALING,
    )
    gateway_subscription_id = models.CharField(
        max_length=100, blank=True, null=True, db_index=True,
    )
    gateway_customer_id = models.CharField(
        max_length=100, blank=True, null=True,
    )
    current_period_end = models.DateTimeField(null=True, blank=True)
    amount = models.PositiveIntegerField(
        default=0, help_text="Valor em centavos",
    )
    billing_cycle = models.CharField(
        max_length=10,
        choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')],
        default='MONTHLY',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['gateway_subscription_id']),
        ]

    def __str__(self):
        return f"{self.tenant.company_name} - {self.plan} ({self.status})"
