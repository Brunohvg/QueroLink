import uuid
from django.db import models
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller

class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELED = 'CANCELED', 'Canceled'
        SUSPENDED = 'SUSPENDED', 'Suspended'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='orders')
    seller = models.ForeignKey(Seller, on_delete=models.SET_NULL, null=True, related_name='orders')
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)
    total_amount = models.PositiveIntegerField(help_text="Value in cents")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'seller', 'created_at']),
        ]

    def __str__(self):
        return f"Order {self.uuid} - {self.customer_name}"

    @property
    def total_amount_decimal(self):
        return f"{self.total_amount / 100:.2f}".replace('.', ',')

class PaymentLink(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment_link')
    short_code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    gateway_url = models.URLField(max_length=500, blank=True, null=True)
    gateway_link_id = models.CharField(max_length=100, blank=True, null=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicks_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['short_code']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Link for Order {self.order.uuid}"
