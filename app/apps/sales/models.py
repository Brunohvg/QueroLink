import uuid
from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.orders.models import Order


class Sale(models.Model):
    class Origin(models.TextChoices):
        LINK = 'LINK', 'Venda via Link de Pagamento'
        MANUAL = 'MANUAL', 'Lançamento Manual'
        IMPORTADA = 'IMPORTADA', 'Importada'

    COMMISSION_ORIGINS = [Origin.MANUAL, Origin.IMPORTADA]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sales')
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name='sales')
    order = models.OneToOneField(
        Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale'
    )
    origin = models.CharField(max_length=10, choices=Origin.choices)
    status = models.CharField(
        max_length=10,
        choices=[('ATIVA', 'Ativa'), ('ESTORNADA', 'Estornada')],
        default='ATIVA',
    )
    amount = models.PositiveIntegerField(help_text="Valor em centavos")
    sale_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_lancadas',
    )
    updated_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sales_editadas',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'seller', 'sale_date']),
            models.Index(fields=['tenant', 'origin']),
            models.Index(fields=['seller', 'sale_date']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['seller', 'sale_date'],
                condition=Q(origin='MANUAL'),
                name='unique_manual_entry_per_day',
            ),
        ]

    def __str__(self):
        return f"Sale {self.uuid} - {self.seller.name} - {self.amount / 100:.2f}"

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError("O valor da venda deve ser maior que zero.")
        if self.origin == self.Origin.LINK and self.order is None:
            raise ValidationError(
                "Venda de origem LINK precisa estar vinculada a um Order."
            )
        if self.seller.tenant_id != self.tenant_id:
            raise ValidationError(
                "O vendedor nao pertence ao tenant da venda."
            )


class SaleChangeLog(models.Model):
    class Action(models.TextChoices):
        UPDATE = 'UPDATE', 'Atualização'
        CREATE_IMPORT = 'CREATE_IMPORT', 'Criação por Importação'
        VOID = 'VOID', 'Estorno'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(
        Sale, on_delete=models.CASCADE, related_name='change_logs',
    )
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='sale_change_logs',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    changed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sale_changes',
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    field_changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'sale']),
            models.Index(fields=['changed_at']),
        ]


class SaleImportBatch(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='sale_import_batches',
    )
    filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sale_imports',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ('PENDING', 'Pendente'),
            ('IMPORTED', 'Importado'),
            ('REJECTED', 'Rejeitado'),
        ],
        default='PENDING',
    )
    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'file_hash']),
            models.Index(fields=['uploaded_at']),
        ]
