import uuid
from pathlib import Path
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from app.apps.accounts.models import Tenant
from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.sellers.validators import normalize_and_validate_cpf, validate_cnpj


DEFAULT_INSTRUCTIONS = 'Apos o vencimento: multa de 2% e juros de 1% ao mes.'


def invoice_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f'receivables/{instance.tenant_id}/{instance.uuid}/invoice-{uuid.uuid4()}{extension}'


class Boleto(models.Model):
    class DocumentType(models.TextChoices):
        CPF = 'CPF', 'CPF'
        CNPJ = 'CNPJ', 'CNPJ'

    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        PAGO = 'PAGO', 'Pago'
        VENCIDO = 'VENCIDO', 'Vencido'
        CANCELADO = 'CANCELADO', 'Cancelado'
        ESTORNADO = 'ESTORNADO', 'Estornado'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='boletos')
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name='boletos')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='boletos_created',
    )

    payer_name = models.CharField(max_length=150)
    payer_document = models.CharField(max_length=18)
    payer_document_type = models.CharField(max_length=4, choices=DocumentType.choices)
    payer_email = models.EmailField(blank=True)
    payer_phone = models.CharField(max_length=20)
    payer_zip_code = models.CharField(max_length=9)
    payer_street = models.CharField(max_length=150)
    payer_number = models.CharField(max_length=20)
    payer_complement = models.CharField(max_length=100, blank=True)
    payer_neighborhood = models.CharField(max_length=100)
    payer_city = models.CharField(max_length=100)
    payer_state = models.CharField(max_length=2)

    amount_cents = models.PositiveIntegerField()
    due_date = models.DateField()
    instructions = models.CharField(max_length=256, default=DEFAULT_INSTRUCTIONS)
    notes = models.CharField(max_length=255, blank=True)

    gateway = models.CharField(max_length=20, default='PAGARME')
    gateway_order_id = models.CharField(max_length=64, blank=True)
    gateway_charge_id = models.CharField(max_length=64, blank=True, db_index=True)
    barcode = models.CharField(max_length=160, blank=True)
    boleto_url = models.URLField(max_length=500, blank=True)
    boleto_pdf_password = models.CharField(max_length=100, blank=True)
    invoice_pdf = models.FileField(upload_to=invoice_upload_to, blank=True, max_length=500)
    invoice_xml = models.FileField(upload_to=invoice_upload_to, blank=True, max_length=500)
    invoice_uploaded_at = models.DateTimeField(null=True, blank=True)
    invoice_uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='boleto_invoices_uploaded',
    )
    customer_snapshot = models.JSONField(null=True, blank=True)

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDENTE,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount_cents = models.PositiveIntegerField(null=True, blank=True)
    launched_sale = models.OneToOneField(
        Sale,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='boleto',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Boleto {self.uuid} - {self.payer_name}'

    def clean(self):
        errors = {}
        document = ''.join(filter(str.isdigit, self.payer_document or ''))
        try:
            if self.payer_document_type == self.DocumentType.CPF:
                document = normalize_and_validate_cpf(document)
            elif self.payer_document_type == self.DocumentType.CNPJ:
                document = validate_cnpj(document)
            else:
                errors['payer_document_type'] = 'Tipo de documento invalido.'
        except ValueError as exc:
            errors['payer_document'] = str(exc)
        self.payer_document = document
        self.payer_zip_code = ''.join(filter(str.isdigit, self.payer_zip_code or ''))
        self.payer_phone = ''.join(filter(str.isdigit, self.payer_phone or ''))
        self.payer_state = (self.payer_state or '').strip().upper()

        if not self.amount_cents or self.amount_cents <= 0:
            errors['amount_cents'] = 'Valor deve ser maior que zero.'
        today = timezone.localdate()
        if not self.due_date or self.due_date < today + timedelta(days=1):
            errors['due_date'] = 'Vencimento deve ser a partir de amanha.'
        elif self.due_date > today + timedelta(days=180):
            errors['due_date'] = 'Vencimento deve ser em ate 180 dias.'
        if len(self.payer_zip_code) != 8:
            errors['payer_zip_code'] = 'CEP deve ter 8 digitos.'
        if len(self.payer_phone) not in (10, 11):
            errors['payer_phone'] = 'Telefone deve ter DDD e 10 ou 11 digitos.'
        if len(self.payer_state) != 2:
            errors['payer_state'] = 'UF deve ter 2 letras.'
        required = (
            'payer_name', 'payer_street', 'payer_number',
            'payer_neighborhood', 'payer_city',
        )
        for field in required:
            if not str(getattr(self, field, '') or '').strip():
                errors[field] = 'Campo obrigatorio.'
        if self.seller_id and self.tenant_id and self.seller.tenant_id != self.tenant_id:
            errors['seller'] = 'Vendedor nao pertence ao tenant.'
        if self.created_by_id and self.tenant_id and self.created_by.tenant_id != self.tenant_id:
            errors['created_by'] = 'Usuario nao pertence ao tenant.'
        if errors:
            raise ValidationError(errors)
