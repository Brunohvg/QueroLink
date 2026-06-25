import uuid
from django.db import models
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
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'phone_hash'],
                name='unique_tenant_phone',
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

    def __str__(self):
        return self.name
