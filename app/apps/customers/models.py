import uuid

from django.conf import settings
from django.db import models

from app.apps.accounts.fields import EncryptedCharField, EncryptedTextField
from app.apps.accounts.models import Tenant


class Customer(models.Model):
    class ConsentStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Nao informado"
        GRANTED = "GRANTED", "Autorizado"
        REVOKED = "REVOKED", "Revogado"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="customers"
    )
    name = EncryptedCharField()
    name_hash = models.CharField(max_length=64, blank=True)
    email = EncryptedCharField(blank=True)
    phone = EncryptedCharField(blank=True)
    document = EncryptedCharField(blank=True)
    document_type = models.CharField(max_length=4, blank=True)
    email_hash = models.CharField(max_length=64, blank=True)
    phone_hash = models.CharField(max_length=64, blank=True)
    document_hash = models.CharField(max_length=64, blank=True)
    zip_code = EncryptedCharField(blank=True)
    street = EncryptedCharField(blank=True)
    number = EncryptedCharField(blank=True)
    complement = EncryptedCharField(blank=True)
    neighborhood = EncryptedCharField(blank=True)
    city = EncryptedCharField(blank=True)
    state = models.CharField(max_length=2, blank=True)
    notes = EncryptedTextField(blank=True)
    marketing_consent = models.CharField(
        max_length=10,
        choices=ConsentStatus.choices,
        default=ConsentStatus.UNKNOWN,
    )
    marketing_consent_at = models.DateTimeField(null=True, blank=True)
    marketing_consent_source = models.CharField(max_length=100, blank=True)
    marketing_consent_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="customer_consents_updated",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name


class CustomerActivity(models.Model):
    class Source(models.TextChoices):
        BOLETO = "BOLETO", "Boleto"
        PAYMENT_LINK = "PAYMENT_LINK", "Link de pagamento"

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="activities"
    )
    source = models.CharField(max_length=20, choices=Source.choices)
    source_uuid = models.UUIDField()
    seller_name = models.CharField(max_length=150, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=30, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
