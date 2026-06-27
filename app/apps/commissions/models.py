import uuid
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller


class CommissionPeriod(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        PARCIALMENTE_FECHADA = 'PARCIALMENTE_FECHADA', 'Parcialmente Fechada'
        FECHADA = 'FECHADA', 'Fechada'
        PARCIALMENTE_PAGA = 'PARCIALMENTE_PAGA', 'Parcialmente Paga'
        PAGA = 'PAGA', 'Paga'
        CANCELADA = 'CANCELADA', 'Cancelada'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='commission_periods'
    )
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    year = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.ABERTA
    )
    expected_working_days = models.PositiveSmallIntegerField(
        default=22, help_text="Dias trabalhados esperados no mes"
    )
    notes = models.TextField(blank=True, default='')

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
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'month', 'year'],
                name='unique_period_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['month', 'year']),
        ]

    def __str__(self):
        return (
            f"Competencia {self.month:02d}/{self.year} "
            f"- {self.tenant.company_name} ({self.status})"
        )

    @classmethod
    def is_locked_for(cls, tenant, sale_date):
        from app.apps.commissions.models import SellerCommission
        locked = SellerCommission.objects.filter(
            seller__tenant=tenant,
            period__month=sale_date.month,
            period__year=sale_date.year,
            status__in=[
                SellerCommission.Status.FECHADA,
                SellerCommission.Status.PAGA,
                SellerCommission.Status.AJUSTADA,
                SellerCommission.Status.CANCELADA,
            ],
        ).values_list('seller_id', flat=True)
        return set(locked)


class SellerCommission(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        FECHADA = 'FECHADA', 'Fechada'
        PAGA = 'PAGA', 'Paga'
        AJUSTADA = 'AJUSTADA', 'Ajustada'
        REABERTA = 'REABERTA', 'Reaberta'
        CANCELADA = 'CANCELADA', 'Cancelada'

    class OperationalStatus(models.TextChoices):
        PRONTO = 'PRONTO', 'Pronto'
        PENDENTE = 'PENDENTE', 'Pendente'
        SEM_LANCAMENTO = 'SEM_LANCAMENTO', 'Sem Lancamento'

    period = models.ForeignKey(
        CommissionPeriod, on_delete=models.CASCADE,
        related_name='seller_commissions',
    )
    seller = models.ForeignKey(
        Seller, on_delete=models.CASCADE, related_name='commissions',
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ABERTA,
        help_text="Status financeiro individual do vendedor",
        db_index=True,
    )
    operational_status = models.CharField(
        max_length=20, choices=OperationalStatus.choices,
        default=OperationalStatus.SEM_LANCAMENTO,
        help_text="Status operacional (dias lancados vs esperados)",
    )

    total_sold_amount = models.PositiveIntegerField(
        default=0, help_text="Total vendido em centavos (ao vivo se ABERTA)"
    )
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.01,
    )
    commission_amount = models.PositiveIntegerField(
        default=0, help_text="Comissao devida em centavos"
    )

    expected_working_days = models.PositiveSmallIntegerField(
        default=22, help_text="Dias esperados (congelado no fechamento)"
    )
    submitted_days_count = models.PositiveSmallIntegerField(
        default=0, help_text="Dias com lancamento manual"
    )
    missing_days_count = models.PositiveSmallIntegerField(
        default=0, help_text="Dias faltantes"
    )

    frozen_total_sold_amount = models.PositiveIntegerField(
        null=True, blank=True, help_text="Total congelado no fechamento"
    )
    frozen_commission_rate = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
    )
    frozen_commission_amount = models.PositiveIntegerField(
        null=True, blank=True, help_text="Comissao congelada no fechamento"
    )

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='commissions_closed',
    )

    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='commissions_reopened',
    )
    reopen_reason = models.TextField(blank=True, null=True)

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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['period', 'seller'],
                name='unique_commission_per_seller',
            ),
        ]
        indexes = [
            models.Index(fields=['period', 'status']),
            models.Index(fields=['seller', 'status']),
        ]

    def __str__(self):
        return f"{self.seller.name} - {self.period} ({self.status})"

    def _compute_working_days(self):
        from app.apps.sales.models import Sale
        sales_dates = Sale.objects.filter(
            tenant=self.period.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            sale_date__year=self.period.year,
            sale_date__month=self.period.month,
        ).dates('sale_date', 'day')
        count = sales_dates.count()
        expected = self.expected_working_days or self.period.expected_working_days or 22
        missing = max(0, expected - count)
        return count, expected, missing

    def update_operational_status(self, commit=True):
        count, expected, missing = self._compute_working_days()
        self.submitted_days_count = count
        self.expected_working_days = expected
        self.missing_days_count = missing
        if count == 0:
            self.operational_status = self.OperationalStatus.SEM_LANCAMENTO
        elif count >= expected:
            self.operational_status = self.OperationalStatus.PRONTO
        else:
            self.operational_status = self.OperationalStatus.PENDENTE
        if commit:
            self.save(update_fields=[
                'submitted_days_count', 'expected_working_days',
                'missing_days_count', 'operational_status',
            ])

    def recalculate(self, commit=True):
        from app.apps.sales.models import Sale
        import calendar
        from decimal import Decimal, ROUND_HALF_UP

        last_day = calendar.monthrange(self.period.year, self.period.month)[1]
        start = f"{self.period.year}-{self.period.month:02d}-01"
        end = f"{self.period.year}-{self.period.month:02d}-{last_day}"

        total = Sale.objects.filter(
            tenant=self.period.tenant,
            seller=self.seller,
            origin=Sale.Origin.MANUAL,
            sale_date__range=(start, end),
        ).aggregate(t=Sum('amount'))['t'] or 0
        self.total_sold_amount = total
        rate = self.seller.commission_rate
        if rate is None or rate <= 0:
            rate = self.seller.tenant.default_commission_rate
        if rate is None or rate <= 0:
            rate = self.commission_rate
        if rate is None or rate <= 0:
            rate = Decimal('0.01')
        self.commission_amount = int((Decimal(str(total)) * Decimal(str(rate))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        self.update_operational_status(commit=False)
        if commit:
            self.save(update_fields=[
                'total_sold_amount', 'commission_amount',
                'submitted_days_count', 'expected_working_days',
                'missing_days_count', 'operational_status',
            ])

    def freeze(self, user, commit=True):
        self.recalculate(commit=False)
        self.frozen_total_sold_amount = self.total_sold_amount
        self.frozen_commission_rate = self.commission_rate
        self.frozen_commission_amount = self.commission_amount
        self.status = self.Status.FECHADA
        self.closed_by = user
        self.closed_at = timezone.now()
        if commit:
            self.save(update_fields=[
                'total_sold_amount', 'commission_amount',
                'submitted_days_count', 'expected_working_days',
                'missing_days_count', 'operational_status',
                'frozen_total_sold_amount', 'frozen_commission_rate',
                'frozen_commission_amount',
                'status', 'closed_by', 'closed_at',
            ])

    def mark_paid(self, user, payment_data, commit=True):
        self.status = self.Status.PAGA
        self.paid_by = user
        self.paid_at = timezone.now()
        self.paid_amount = self.frozen_commission_amount or self.commission_amount
        self.payment_date = payment_data.get('payment_date')
        self.payment_method = (payment_data.get('payment_method') or '').strip() or None
        self.payment_notes = (payment_data.get('payment_notes') or '').strip() or None
        if commit:
            self.save(update_fields=[
                'status', 'paid_by', 'paid_at', 'paid_amount',
                'payment_date', 'payment_method', 'payment_notes',
            ])

    def reopen(self, user, reason, commit=True):
        self.status = self.Status.REABERTA
        self.reopened_by = user
        self.reopened_at = timezone.now()
        self.reopen_reason = reason
        self.closed_at = None
        self.closed_by = None
        self.frozen_total_sold_amount = None
        self.frozen_commission_rate = None
        self.frozen_commission_amount = None
        if commit:
            self.save(update_fields=[
                'status', 'reopened_by', 'reopened_at', 'reopen_reason',
                'closed_at', 'closed_by',
                'frozen_total_sold_amount', 'frozen_commission_rate',
                'frozen_commission_amount',
            ])

    @property
    def is_editable(self):
        return self.status in (
            self.Status.ABERTA,
            self.Status.REABERTA,
        )

    @property
    def is_paid(self):
        return self.status == self.Status.PAGA


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