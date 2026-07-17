import uuid

from django.db import models
from django.db.models import Q

from app.apps.accounts.fields import EncryptedCharField
from app.apps.accounts.models import Tenant


class Customer(models.Model):
    class ConsentStatus(models.TextChoices):
        UNKNOWN = 'UNKNOWN', 'Nao informado'
        GRANTED = 'GRANTED', 'Autorizado'
        REVOKED = 'REVOKED', 'Revogado'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='customers'
    )
    name = EncryptedCharField()
    email = EncryptedCharField(blank=True)
    phone = EncryptedCharField(blank=True)
    document = EncryptedCharField(blank=True)
    document_type = models.CharField(max_length=4, blank=True)
    name_hash = models.CharField(max_length=64, blank=True)
    document_hash = models.CharField(max_length=64, blank=True)
    email_hash = models.CharField(max_length=64, blank=True)
    phone_hash = models.CharField(max_length=64, blank=True)
    operational_consent = models.CharField(
        max_length=10,
        choices=ConsentStatus.choices,
        default=ConsentStatus.UNKNOWN,
    )
    marketing_consent = models.CharField(
        max_length=10,
        choices=ConsentStatus.choices,
        default=ConsentStatus.UNKNOWN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'document_hash'],
                condition=~Q(document_hash=''),
                name='uniq_customer_tenant_document',
            ),
        ]

    def __str__(self):
        return self.name


class CustomerActivity(models.Model):
    class Source(models.TextChoices):
        BOLETO = 'BOLETO', 'Boleto'
        PAYMENT_LINK = 'PAYMENT_LINK', 'Payment Link'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='customer_activities'
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name='activities'
    )
    source = models.CharField(max_length=20, choices=Source.choices)
    source_uuid = models.UUIDField()
    seller_name = models.CharField(max_length=150, blank=True)
    amount_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=30, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-occurred_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'source', 'source_uuid'],
                name='uniq_customer_activity_source',
            ),
        ]


class CustomerIdentityConflict(models.Model):
    class IdentifierType(models.TextChoices):
        DOCUMENT = 'DOCUMENT', 'Documento'
        EMAIL = 'EMAIL', 'E-mail'
        PHONE = 'PHONE', 'Telefone'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='customer_identity_conflicts'
    )
    selected_customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='identity_conflicts_selected',
    )
    conflicting_customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='identity_conflicts_conflicting',
    )
    identifier_type = models.CharField(
        max_length=10, choices=IdentifierType.choices
    )
    identifier_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=20)
    source_uuid = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'identifier_type', 'identifier_hash',
                        'selected_customer', 'conflicting_customer'],
                name='uniq_customer_identity_conflict',
            ),
        ]
