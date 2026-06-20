import uuid
from django.db import models
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller

class CommissionPeriod(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        EM_CONFERENCIA = 'EM_CONFERENCIA', 'Em Conferência'
        ENVIADA_FINANCEIRO = 'ENVIADA_FINANCEIRO', 'Enviada ao Financeiro'
        APROVADA = 'APROVADA', 'Aprovada para Pagamento'
        PAGA = 'PAGA', 'Paga'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='commission_periods')
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTA)
    sent_to_financial_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'month', 'year')
        indexes = [
            models.Index(fields=['tenant', 'status']),
        ]

    def __str__(self):
        return f"Competência {self.month:02d}/{self.year} - {self.tenant.company_name} ({self.status})"


class SellerCommission(models.Model):
    """Consolidado de comissão de UM vendedor em UMA competência mensal."""
    period = models.ForeignKey(CommissionPeriod, on_delete=models.CASCADE, related_name='seller_commissions')
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='commissions')
    total_sold_amount = models.PositiveIntegerField(default=0, help_text="Total vendido em centavos")
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    commission_amount = models.PositiveIntegerField(default=0, help_text="Comissão devida em centavos")
    payment_date = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=50, blank=True, null=True)
    payment_notes = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ('period', 'seller')

    def __str__(self):
        return f"{self.seller.name} - {self.period}"

    def recalculate(self):
        """Recalcula total vendido e comissão a partir das Sales do período."""
        from app.apps.sales.models import Sale
        import calendar
        last_day = calendar.monthrange(self.period.year, self.period.month)[1]
        start = f"{self.period.year}-{self.period.month:02d}-01"
        end = f"{self.period.year}-{self.period.month:02d}-{last_day}"
        sales = Sale.objects.filter(
            seller=self.seller,
            sale_date__range=(start, end),
        )
        total = sum(s.amount for s in sales)
        self.total_sold_amount = total
        self.commission_amount = int(total * self.commission_rate)
