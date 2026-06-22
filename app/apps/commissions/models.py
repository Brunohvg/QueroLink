import uuid
from django.db import models
from django.conf import settings
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller


class CommissionPeriod(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        FECHADA = 'FECHADA', 'Fechada'
        PAGA = 'PAGA', 'Paga'
        AJUSTADA = 'AJUSTADA', 'Ajustada'
        CANCELADA = 'CANCELADA', 'Cancelada'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='commission_periods'
    )
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ABERTA
    )

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='periods_closed',
    )

    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='periods_paid',
    )

    adjusted_at = models.DateTimeField(null=True, blank=True)
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='periods_adjusted',
    )
    adjustment_reason = models.TextField(blank=True, null=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='periods_cancelled',
    )
    cancel_reason = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'month', 'year')
        indexes = [
            models.Index(fields=['tenant', 'status']),
        ]

    def __str__(self):
        return (
            f"Competencia {self.month:02d}/{self.year} "
            f"- {self.tenant.company_name} ({self.status})"
        )

    @classmethod
    def is_locked_for(cls, tenant, sale_date):
        locked_statuses = [
            cls.Status.FECHADA,
            cls.Status.PAGA,
            cls.Status.AJUSTADA,
            cls.Status.CANCELADA,
        ]
        return cls.objects.filter(
            tenant=tenant,
            month=sale_date.month,
            year=sale_date.year,
            status__in=locked_statuses,
        ).exists()

    @classmethod
    def is_closed_or_paid_for(cls, tenant, sale_date):
        return cls.objects.filter(
            tenant=tenant,
            month=sale_date.month,
            year=sale_date.year,
            status__in=[cls.Status.FECHADA, cls.Status.PAGA],
        ).exists()


class SellerCommission(models.Model):

    period = models.ForeignKey(
        CommissionPeriod, on_delete=models.CASCADE,
        related_name='seller_commissions',
    )
    seller = models.ForeignKey(
        Seller, on_delete=models.CASCADE, related_name='commissions',
    )

    total_sold_amount = models.PositiveIntegerField(
        default=0, help_text="Total vendido em centavos"
    )
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.01,
    )
    commission_amount = models.PositiveIntegerField(
        default=0, help_text="Comissao devida em centavos"
    )

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='commissions_closed',
    )

    payment_date = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='commissions_paid',
    )
    paid_amount = models.PositiveIntegerField(
        null=True, blank=True, help_text="Valor efetivamente pago em centavos"
    )
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    payment_notes = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ('period', 'seller')

    def __str__(self):
        return f"{self.seller.name} - {self.period}"

    def recalculate(self, commit=True):
        from app.apps.sales.models import Sale
        import calendar

        last_day = calendar.monthrange(self.period.year, self.period.month)[1]
        start = f"{self.period.year}-{self.period.month:02d}-01"
        end = f"{self.period.year}-{self.period.month:02d}-{last_day}"

        sales = Sale.objects.filter(
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            sale_date__range=(start, end),
        )
        total = sum(s.amount for s in sales)
        self.total_sold_amount = total
        self.commission_amount = round(float(total) * float(self.commission_rate))
        if commit:
            self.save(update_fields=['total_sold_amount', 'commission_amount'])

    def freeze(self, user, commit=True):
        self.recalculate(commit=False)
        self.closed_by = user
        from django.utils import timezone
        self.closed_at = timezone.now()
        if commit:
            self.save(update_fields=[
                'total_sold_amount', 'commission_amount',
                'closed_by', 'closed_at',
            ])

    @property
    def is_frozen(self):
        return self.period.status in (
            CommissionPeriod.Status.FECHADA,
            CommissionPeriod.Status.PAGA,
            CommissionPeriod.Status.AJUSTADA,
        )


class CommissionAdjustment(models.Model):
    seller_commission = models.ForeignKey(
        SellerCommission, on_delete=models.CASCADE,
        related_name='adjustments',
    )
    previous_amount = models.PositiveIntegerField(
        help_text="Valor da comissao antes do ajuste em centavos"
    )
    new_amount = models.PositiveIntegerField(
        help_text="Valor da comissao depois do ajuste em centavos"
    )
    difference = models.IntegerField(
        help_text="Diferenca (positivo = aumento, negativo = reducao) em centavos"
    )
    reason = models.TextField()
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='adjustments_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"Ajuste {self.seller_commission} "
            f"({self.previous_amount} -> {self.new_amount})"
        )
