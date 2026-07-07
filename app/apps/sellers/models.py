import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from app.apps.accounts.models import Tenant
from app.apps.accounts.fields import EncryptedCharField, compute_hash

class Seller(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sellers')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='seller_profile',
    )
    name = models.CharField(max_length=100)
    phone = EncryptedCharField(max_length=600)
    phone_hash = models.CharField(max_length=64, blank=True, null=True)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    cpf = models.CharField(
        max_length=14, blank=True, null=True,
        help_text='CPF do vendedor (para folha/contabilidade). Formato: 000.000.000-00',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'phone_hash'],
                name='unique_tenant_phone',
            ),
            models.UniqueConstraint(
                fields=['tenant', 'cpf'],
                condition=models.Q(cpf__isnull=False),
                name='unique_cpf_per_tenant',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def save(self, *args, **kwargs):
        if self.phone and (not self.phone_hash or self._phone_changed()):
            self.phone_hash = compute_hash(self.phone)
        super().save(*args, **kwargs)

    def _phone_changed(self):
        if not self.pk:
            return True
        try:
            old = Seller.objects.get(pk=self.pk)
            return old.phone != self.phone
        except Seller.DoesNotExist:
            return True

    def clean(self):
        if self.cpf:
            from .validators import normalize_and_validate_cpf
            try:
                digits = normalize_and_validate_cpf(self.cpf)
            except ValueError as e:
                raise ValidationError({'cpf': str(e)})
            qs = Seller.objects.filter(tenant=self.tenant, cpf=digits).exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'cpf': 'Ja existe um vendedor com este CPF.'})
            self.cpf = digits

    @property
    def cpf_formatted(self):
        if not self.cpf or len(self.cpf) != 11:
            return self.cpf or ''
        return f'{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}'

    def __str__(self):
        return self.name


class SellerGoal(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller = models.ForeignKey(
        Seller, on_delete=models.CASCADE, related_name='goals',
    )
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    target_amount = models.PositiveIntegerField(help_text="Meta em centavos")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['seller', 'month', 'year'],
                name='unique_goal_per_seller_period',
            ),
        ]

    def __str__(self):
        return f"Meta {self.seller.name} - {self.month:02d}/{self.year}: {self.target_amount}"

    @property
    def progress_percent(self):
        from app.apps.sales.models import Sale
        from django.db.models import Sum
        total = Sale.objects.filter(
            seller=self.seller,
            status='ATIVA',
            sale_date__year=self.year,
            sale_date__month=self.month,
        ).aggregate(t=Sum('amount'))['t'] or 0
        if self.target_amount <= 0:
            return 0
        return min(100, int(total * 100 / self.target_amount))
