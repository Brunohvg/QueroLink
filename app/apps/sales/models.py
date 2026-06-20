import uuid
from django.db import models
from django.core.exceptions import ValidationError
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.orders.models import Order

class Sale(models.Model):
    class Origin(models.TextChoices):
        LINK = 'LINK', 'Venda via Link de Pagamento'
        MANUAL = 'MANUAL', 'Lançamento Manual'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sales')
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='sales')
    order = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale')
    origin = models.CharField(max_length=10, choices=Origin.choices)
    amount = models.PositiveIntegerField(help_text="Valor em centavos")
    sale_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales_lancadas')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'seller', 'sale_date']),
            models.Index(fields=['tenant', 'origin']),
        ]

    def __str__(self):
        return f"Sale {self.uuid} - {self.seller.name} - {self.amount/100:.2f}"

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError("O valor da venda deve ser maior que zero.")
        if self.origin == self.Origin.LINK and self.order is None:
            raise ValidationError("Venda de origem LINK precisa estar vinculada a um Order.")
