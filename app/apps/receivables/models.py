import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models
from django.utils import timezone

from app.apps.accounts.fields import EncryptedCharField
from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller
from app.apps.sellers.validators import normalize_and_validate_cpf


def _digits(value):
    return ''.join(filter(str.isdigit, value or ''))


def invoice_pdf_upload_to(instance, filename):
    return f'receivables_private/{instance.tenant_id}/{instance.uuid}/invoice.pdf'


def invoice_xml_upload_to(instance, filename):
    return f'receivables_private/{instance.tenant_id}/{instance.uuid}/invoice.xml'


def _validate_cnpj(value):
    digits = _digits(value)
    if len(digits) != 14:
        raise ValueError('CNPJ deve ter 14 digitos.')
    if digits == digits[0] * 14:
        raise ValueError('CNPJ invalido.')

    def check_digit(base, weights):
        remainder = sum(int(n) * w for n, w in zip(base, weights)) % 11
        return 0 if remainder < 2 else 11 - remainder

    first = check_digit(digits[:12], (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    second = check_digit(
        digits[:12] + str(first),
        (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
    )
    if digits[-2:] != f'{first}{second}':
        raise ValueError('CNPJ invalido.')
    return digits


class Boleto(models.Model):
    class DocumentType(models.TextChoices):
        CPF = 'CPF', 'CPF'
        CNPJ = 'CNPJ', 'CNPJ'

    class Status(models.TextChoices):
        CRIANDO = 'CRIANDO', 'Criando'
        PENDENTE = 'PENDENTE', 'Pendente'
        PAGO = 'PAGO', 'Pago'
        VENCIDO = 'VENCIDO', 'Vencido'
        CANCEL_PEND = 'CANCEL_PEND', 'Cancelamento pendente'
        CANCELADO = 'CANCELADO', 'Cancelado'
        ESTORNADO = 'ESTORNADO', 'Estornado'
        FALHOU = 'FALHOU', 'Falhou'

    ALLOWED_TRANSITIONS = {
        Status.CRIANDO: {Status.PENDENTE, Status.FALHOU},
        Status.PENDENTE: {
            Status.PAGO,
            Status.VENCIDO,
            Status.CANCEL_PEND,
            Status.FALHOU,
        },
        Status.VENCIDO: {Status.PAGO, Status.CANCEL_PEND},
        Status.CANCEL_PEND: {
            Status.CANCELADO,
            Status.PENDENTE,
            Status.PAGO,
            Status.FALHOU,
        },
        Status.PAGO: {Status.ESTORNADO},
        Status.CANCELADO: set(),
        Status.ESTORNADO: set(),
        Status.FALHOU: {Status.CRIANDO},
    }

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='boletos')
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name='boletos')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='boletos_created',
    )

    payer_name = EncryptedCharField()
    payer_document = EncryptedCharField()
    payer_document_type = models.CharField(max_length=4, choices=DocumentType.choices)
    payer_email = EncryptedCharField(blank=True)
    payer_phone = EncryptedCharField()
    payer_zip_code = EncryptedCharField()
    payer_street = EncryptedCharField()
    payer_number = EncryptedCharField()
    payer_complement = EncryptedCharField(blank=True)
    payer_neighborhood = EncryptedCharField()
    payer_city = EncryptedCharField()
    payer_state = EncryptedCharField()

    amount_cents = models.PositiveIntegerField()
    due_date = models.DateField()
    instructions = models.CharField(max_length=256, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    provider = models.CharField(max_length=20, default='PAGARME')
    provider_order_id = models.CharField(max_length=100, blank=True)
    provider_charge_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(max_length=100)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.CRIANDO,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount_cents = models.PositiveIntegerField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    refunded_amount_cents = models.PositiveIntegerField(null=True, blank=True)
    refund_reason = models.CharField(max_length=255, blank=True)
    last_provider_status = models.CharField(max_length=50, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    operation_error_code = models.CharField(max_length=50, blank=True)
    operation_error_message = models.CharField(max_length=255, blank=True)
    provider_barcode = models.CharField(max_length=255, blank=True)
    provider_url = models.URLField(max_length=500, blank=True)
    invoice_pdf = models.FileField(
        upload_to=invoice_pdf_upload_to, max_length=255, blank=True
    )
    invoice_pdf_uploaded_at = models.DateTimeField(null=True, blank=True)
    invoice_pdf_uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_pdfs_uploaded',
    )
    invoice_xml = models.FileField(
        upload_to=invoice_xml_upload_to, max_length=255, blank=True
    )
    invoice_xml_uploaded_at = models.DateTimeField(null=True, blank=True)
    invoice_xml_uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invoice_xmls_uploaded',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'idempotency_key'],
                name='uniq_boleto_tenant_idem',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'provider', 'provider_order_id']),
            models.Index(fields=['tenant', 'provider', 'provider_charge_id']),
        ]

    def __str__(self):
        return f'Boleto {self.uuid}'

    @staticmethod
    def sanitize_error_message(message):
        sanitized = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(message or ''))
        return ' '.join(sanitized.split())[:255]

    def set_operation_error(self, code, message):
        self.operation_error_code = str(code or '')[:50]
        self.operation_error_message = self.sanitize_error_message(message)

    def can_transition_to(self, new_status):
        if new_status == self.status:
            return True
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status):
        if new_status not in self.Status.values:
            raise ValidationError({'status': 'Status de boleto invalido.'})
        if not self.can_transition_to(new_status):
            raise ValidationError(
                {'status': f'Transicao de {self.status} para {new_status} nao permitida.'}
            )
        self.status = new_status

    def clean(self):
        super().clean()
        errors = {}

        document = _digits(self.payer_document)
        try:
            if self.payer_document_type == self.DocumentType.CPF:
                document = normalize_and_validate_cpf(document)
            elif self.payer_document_type == self.DocumentType.CNPJ:
                document = _validate_cnpj(document)
            else:
                errors['payer_document_type'] = 'Tipo de documento invalido.'
        except ValueError as exc:
            errors['payer_document'] = str(exc)
        self.payer_document = document

        self.payer_phone = _digits(self.payer_phone)
        self.payer_zip_code = _digits(self.payer_zip_code)
        self.payer_state = (self.payer_state or '').strip().upper()
        self.idempotency_key = (self.idempotency_key or '').strip()
        self.operation_error_message = self.sanitize_error_message(
            self.operation_error_message
        )

        if not self.idempotency_key:
            errors['idempotency_key'] = 'Chave de idempotencia obrigatoria.'
        if not self.amount_cents or self.amount_cents <= 0:
            errors['amount_cents'] = 'Valor deve ser maior que zero.'
        today = timezone.localdate()
        if not self.due_date or self.due_date < today + timedelta(days=1):
            errors['due_date'] = 'Vencimento deve ser a partir de amanha.'
        elif self.due_date > today + timedelta(days=180):
            errors['due_date'] = 'Vencimento deve ser em ate 180 dias.'
        if len(self.payer_phone) not in (10, 11):
            errors['payer_phone'] = 'Telefone deve ter DDD e 10 ou 11 digitos.'
        if len(self.payer_zip_code) != 8:
            errors['payer_zip_code'] = 'CEP deve ter 8 digitos.'
        if len(self.payer_state) != 2 or not self.payer_state.isalpha():
            errors['payer_state'] = 'UF deve ter 2 letras.'
        if self.payer_email:
            try:
                validate_email(self.payer_email)
            except ValidationError:
                errors['payer_email'] = 'E-mail invalido.'
        for field in (
            'payer_name',
            'payer_street',
            'payer_number',
            'payer_neighborhood',
            'payer_city',
        ):
            if not str(getattr(self, field, '') or '').strip():
                errors[field] = 'Campo obrigatorio.'
        if (
            self.seller_id
            and self.tenant_id
            and self.seller.tenant_id != self.tenant_id
        ):
            errors['seller'] = 'Vendedor nao pertence ao tenant.'
        if (
            self.created_by_id
            and self.tenant_id
            and self.created_by.tenant_id != self.tenant_id
        ):
            errors['created_by'] = 'Usuario nao pertence ao tenant.'
        if errors:
            raise ValidationError(errors)


class IntegrationOutbox(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        PROCESSED = 'PROCESSED', 'Processed'
        FAILED = 'FAILED', 'Failed'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='receivables_outbox_events',
    )
    aggregate_type = models.CharField(max_length=50)
    aggregate_uuid = models.UUIDField()
    event_type = models.CharField(max_length=100)
    event_key = models.CharField(max_length=150)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['available_at', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'event_key'],
                name='uniq_outbox_tenant_event_key',
            ),
        ]
        indexes = [
            models.Index(fields=['status', 'available_at']),
        ]

    @staticmethod
    def sanitize_error(message):
        sanitized = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(message or ''))
        return ' '.join(sanitized.split())[:255]


class ReceivableNotificationDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENDING = 'SENDING', 'Sending'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Failed'
        DEAD = 'DEAD', 'Dead'
        SKIPPED = 'SKIPPED', 'Skipped'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    outbox_event = models.ForeignKey(
        IntegrationOutbox, on_delete=models.CASCADE,
        related_name='deliveries', null=True, blank=True,
    )
    channel = models.CharField(max_length=20)
    recipient_hash = models.CharField(max_length=64)
    delivery_key = models.CharField(max_length=150)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    sent_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    skip_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'delivery_key'],
                name='uniq_delivery_tenant_key',
            ),
        ]

    @staticmethod
    def sanitize_error(message):
        sanitized = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(message or ''))
        return ' '.join(sanitized.split())[:255]


class ReceivableAllocation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        REVERSED = 'REVERSED', 'Reversed'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='receivable_allocations'
    )
    boleto = models.ForeignKey(
        Boleto, on_delete=models.PROTECT, related_name='allocations'
    )
    sale = models.ForeignKey(
        'sales.Sale', on_delete=models.PROTECT, related_name='receivable_allocations'
    )
    amount_cents = models.PositiveIntegerField()
    sale_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='receivable_allocations_created',
    )
    allocated_at = models.DateTimeField(default=timezone.now)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'boleto'],
                name='uniq_receivable_allocation_boleto',
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'sale']),
        ]


class CommissionImpactReview(models.Model):
    class ImpactType(models.TextChoices):
        ALLOCATION = 'ALLOCATION', 'Allocation'
        REFUND = 'REFUND', 'Refund'
        CHARGEBACK = 'CHARGEBACK', 'Chargeback'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        APPLIED = 'APPLIED', 'Applied'
        REJECTED = 'REJECTED', 'Rejected'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='commission_impact_reviews')
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name='commission_impact_reviews')
    allocation = models.ForeignKey(ReceivableAllocation, on_delete=models.PROTECT, related_name='commission_impact_reviews')
    source_period = models.ForeignKey('commissions.CommissionPeriod', on_delete=models.PROTECT, related_name='receivable_impact_reviews')
    seller_commission = models.ForeignKey('commissions.SellerCommission', on_delete=models.PROTECT, related_name='receivable_impact_reviews')
    impact_type = models.CharField(max_length=12, choices=ImpactType.choices)
    delta_sale_cents = models.IntegerField()
    estimated_commission_delta_cents = models.IntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='commission_impact_reviews')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tenant', 'allocation', 'impact_type'], name='uniq_commission_impact_review')]
