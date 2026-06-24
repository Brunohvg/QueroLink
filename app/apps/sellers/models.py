import uuid
from django.db import models
from django.conf import settings
from app.apps.accounts.models import Tenant

class Seller(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sellers')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='seller_profile',
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'phone'],
                name='unique_tenant_phone',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return self.name
