from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from app.apps.accounts.models import User
from app.apps.sellers.models import Seller
from app.apps.sellers.validators import normalize_and_validate_cpf, validate_cnpj

from .models import Boleto
from .services import BoletoServiceError, create_boleto


class BoletoSerializer(serializers.ModelSerializer):
    seller_uuid = serializers.UUIDField(write_only=True, required=False)
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    launched_sale_uuid = serializers.UUIDField(source='launched_sale_id', read_only=True)
    has_invoice_pdf = serializers.SerializerMethodField()
    has_invoice_xml = serializers.SerializerMethodField()

    class Meta:
        model = Boleto
        fields = [
            'uuid', 'seller_uuid', 'seller_name', 'payer_name',
            'payer_document', 'payer_document_type', 'payer_email',
            'payer_phone', 'payer_zip_code', 'payer_street', 'payer_number',
            'payer_complement', 'payer_neighborhood', 'payer_city',
            'payer_state', 'amount_cents', 'due_date', 'instructions',
            'notes', 'gateway', 'gateway_order_id', 'gateway_charge_id',
            'barcode', 'boleto_url', 'boleto_pdf_password', 'status',
            'has_invoice_pdf', 'has_invoice_xml', 'invoice_uploaded_at',
            'status_label', 'paid_at', 'paid_amount_cents',
            'launched_sale_uuid', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'uuid', 'seller_name', 'gateway', 'gateway_order_id',
            'gateway_charge_id', 'barcode', 'boleto_url',
            'boleto_pdf_password', 'has_invoice_pdf', 'has_invoice_xml',
            'invoice_uploaded_at', 'status', 'status_label', 'paid_at',
            'paid_amount_cents', 'launched_sale_uuid', 'created_at',
            'updated_at',
        ]

    def get_has_invoice_pdf(self, obj):
        return bool(obj.invoice_pdf)

    def get_has_invoice_xml(self, obj):
        return bool(obj.invoice_xml)

    def validate_payer_document(self, value):
        return ''.join(filter(str.isdigit, value or ''))

    def validate_payer_phone(self, value):
        digits = ''.join(filter(str.isdigit, value or ''))
        if len(digits) not in (10, 11):
            raise serializers.ValidationError(
                'Telefone deve ter DDD e 10 ou 11 digitos.'
            )
        return digits

    def validate_payer_zip_code(self, value):
        digits = ''.join(filter(str.isdigit, value or ''))
        if len(digits) != 8:
            raise serializers.ValidationError('CEP deve ter 8 digitos.')
        return digits

    def validate_payer_state(self, value):
        value = (value or '').strip().upper()
        if len(value) != 2:
            raise serializers.ValidationError('UF deve ter 2 letras.')
        return value

    def validate_amount_cents(self, value):
        if not isinstance(value, int) or value <= 0:
            raise serializers.ValidationError('Valor deve ser maior que zero.')
        return value

    def validate_due_date(self, value):
        today = timezone.localdate()
        if value < today + timedelta(days=1):
            raise serializers.ValidationError(
                'Vencimento deve ser a partir de amanha.'
            )
        if value > today + timedelta(days=180):
            raise serializers.ValidationError(
                'Vencimento deve ser em ate 180 dias.'
            )
        return value

    def validate_instructions(self, value):
        if len(value or '') > 256:
            raise serializers.ValidationError('Maximo de 256 caracteres.')
        return value

    def validate(self, attrs):
        document_type = attrs.get('payer_document_type')
        document = attrs.get('payer_document', '')
        try:
            if document_type == Boleto.DocumentType.CPF:
                attrs['payer_document'] = normalize_and_validate_cpf(document)
            elif document_type == Boleto.DocumentType.CNPJ:
                attrs['payer_document'] = validate_cnpj(document)
            else:
                raise serializers.ValidationError({
                    'payer_document_type': 'Tipo de documento invalido.',
                })
        except ValueError as exc:
            raise serializers.ValidationError({'payer_document': str(exc)}) from exc
        required = (
            'payer_name', 'payer_street', 'payer_number',
            'payer_neighborhood', 'payer_city',
        )
        errors = {
            field: 'Campo obrigatorio.'
            for field in required
            if not str(attrs.get(field) or '').strip()
        }
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        request = self.context['request']
        tenant = request.user.tenant
        seller_uuid = validated_data.pop('seller_uuid', None)
        if request.user.role == User.Role.SELLER:
            try:
                seller = request.user.seller_profile
            except Seller.DoesNotExist as exc:
                raise serializers.ValidationError(
                    {'seller_uuid': 'Perfil de vendedor nao encontrado.'}
                ) from exc
        else:
            if not seller_uuid:
                raise serializers.ValidationError({'seller_uuid': 'Selecione o vendedor.'})
            try:
                seller = Seller.objects.get(
                    tenant=tenant, uuid=seller_uuid, is_active=True,
                )
            except Seller.DoesNotExist as exc:
                raise serializers.ValidationError({'seller_uuid': 'Vendedor invalido.'}) from exc
        try:
            return create_boleto(tenant, seller, request.user, validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        except BoletoServiceError as exc:
            raise serializers.ValidationError({'detail': str(exc)}) from exc
