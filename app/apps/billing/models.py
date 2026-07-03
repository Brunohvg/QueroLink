import uuid
from django.db import models
from app.apps.accounts.models import Tenant


class Subscription(models.Model):
    class Status(models.TextChoices):
        TRIALING = 'TRIALING', 'Trialing'
        ACTIVE = 'ACTIVE', 'Active'
        PAST_DUE = 'PAST_DUE', 'Past Due'
        CANCELED = 'CANCELED', 'Canceled'

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
