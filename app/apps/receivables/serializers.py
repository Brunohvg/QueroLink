from rest_framework import serializers

from app.apps.accounts.models import User, tenant_has_feature

from .models import Boleto, ReceivableAllocation, CommissionImpactReview


class BoletoListSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    status_display = serializers.SerializerMethodField()
    amount_formatted = serializers.SerializerMethodField()
    barcode = serializers.CharField(source='provider_barcode', read_only=True)
    digitable_line = serializers.CharField(
        source='provider_digitable_line', read_only=True
    )
    boleto_url = serializers.URLField(source='provider_url', read_only=True)

    class Meta:
        model = Boleto
        fields = [
            'uuid', 'seller_name', 'amount_cents', 'amount_formatted',
            'status', 'status_display', 'due_date', 'paid_at',
            'paid_amount_cents', 'created_at',
            'provider_barcode', 'provider_url',
            'barcode', 'digitable_line', 'boleto_url',
        ]

    def get_status_display(self, obj):
        return obj.get_status_display()

    def get_amount_formatted(self, obj):
        cents = obj.amount_cents
        return f'{cents // 100},{cents % 100:02d}'


class BoletoDetailSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True
    )
    status_display = serializers.SerializerMethodField()
    amount_formatted = serializers.SerializerMethodField()
    has_invoice_pdf = serializers.SerializerMethodField()
    has_invoice_xml = serializers.SerializerMethodField()
    barcode = serializers.CharField(source='provider_barcode', read_only=True)
    digitable_line = serializers.CharField(
        source='provider_digitable_line', read_only=True
    )
    boleto_url = serializers.URLField(source='provider_url', read_only=True)

    class Meta:
        model = Boleto
        fields = [
            'uuid', 'seller_name', 'created_by_name',
            'amount_cents', 'amount_formatted',
            'status', 'status_display',
            'due_date', 'paid_at', 'paid_amount_cents',
            'instructions', 'notes',
            'provider', 'provider_order_id', 'provider_charge_id',
            'last_provider_status', 'operation_error_code',
            'operation_error_message',
            'has_invoice_pdf', 'has_invoice_xml',
            'provider_barcode', 'provider_url',
            'barcode', 'digitable_line', 'boleto_url',
            'created_at', 'updated_at',
        ]

    def get_status_display(self, obj):
        return obj.get_status_display()

    def get_amount_formatted(self, obj):
        cents = obj.amount_cents
        return f'{cents // 100},{cents % 100:02d}'

    def get_has_invoice_pdf(self, obj):
        return bool(obj.invoice_pdf.name)

    def get_has_invoice_xml(self, obj):
        return bool(obj.invoice_xml.name)


class BoletoCreateSerializer(serializers.Serializer):
    seller_uuid = serializers.UUIDField(required=False)
    payer_name = serializers.CharField(max_length=255)
    payer_document = serializers.CharField(max_length=20)
    payer_document_type = serializers.ChoiceField(
        choices=['CPF', 'CNPJ']
    )
    payer_email = serializers.EmailField(required=False, allow_blank=True, default='')
    payer_phone = serializers.CharField(max_length=20)
    payer_zip_code = serializers.CharField(max_length=10)
    payer_street = serializers.CharField(max_length=255)
    payer_number = serializers.CharField(max_length=20)
    payer_complement = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=''
    )
    payer_neighborhood = serializers.CharField(max_length=255)
    payer_city = serializers.CharField(max_length=255)
    payer_state = serializers.CharField(max_length=2)
    amount_cents = serializers.IntegerField(min_value=100)
    due_date = serializers.DateField()
    instructions = serializers.CharField(
        max_length=256, required=False, allow_blank=True, default=''
    )
    notes = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=''
    )

    def validate_seller_uuid(self, value):
        from app.apps.sellers.models import Seller
        request = self.context.get('request')
        if not request:
            return value
        if request.user.role == User.Role.SELLER:
            raise serializers.ValidationError(
                'Vendedor nao pode escolher seller_uuid.'
            )
        try:
            seller = Seller.objects.get(uuid=value, tenant=request.user.tenant)
        except Seller.DoesNotExist:
            raise serializers.ValidationError('Vendedor nao encontrado.')
        return seller

    def validate_payer_email(self, value):
        return value or ''

    def validate_payer_complement(self, value):
        return value or ''

    def validate_instructions(self, value):
        return value or ''

    def validate_notes(self, value):
        return value or ''

    def validate(self, attrs):
        from app.apps.sellers.validators import (
            normalize_and_validate_cpf,
        )
        doc = attrs.get('payer_document', '')
        doc_type = attrs.get('payer_document_type', 'CPF')
        if doc_type == 'CPF':
            try:
                normalized = normalize_and_validate_cpf(doc)
            except ValueError as e:
                raise serializers.ValidationError(
                    {'payer_document': str(e)}
                )
            attrs['payer_document'] = normalized
        else:
            from .models import _validate_cnpj
            try:
                _validate_cnpj(doc)
            except ValueError as e:
                raise serializers.ValidationError(
                    {'payer_document': str(e)}
                )
        phone = attrs.get('payer_phone', '')
        digits = ''.join(filter(str.isdigit, phone))
        if len(digits) < 10 or len(digits) > 11:
            raise serializers.ValidationError(
                {'payer_phone': 'Telefone deve ter 10 ou 11 digitos.'}
            )
        attrs['payer_phone'] = digits

        zip_digits = ''.join(
            filter(str.isdigit, attrs.get('payer_zip_code', ''))
        )
        if len(zip_digits) != 8:
            raise serializers.ValidationError(
                {'payer_zip_code': 'CEP deve ter 8 digitos.'}
            )
        attrs['payer_zip_code'] = zip_digits

        if len(attrs.get('payer_state', '')) != 2:
            raise serializers.ValidationError(
                {'payer_state': 'UF deve ter 2 caracteres.'}
            )

        today = timezone.localdate()
        if attrs['due_date'] <= today:
            raise serializers.ValidationError(
                {'due_date': 'Vencimento deve ser futura.'}
            )
        if attrs['due_date'] > today + timedelta(days=180):
            raise serializers.ValidationError(
                {'due_date': 'Vencimento maximo de 180 dias.'}
            )

        return attrs


class AllocationSerializer(serializers.ModelSerializer):
    boleto_uuid = serializers.UUIDField(source='boleto.uuid', read_only=True)
    seller_name = serializers.CharField(
        source='boleto.seller.name', read_only=True
    )

    class Meta:
        model = ReceivableAllocation
        fields = [
            'uuid', 'boleto_uuid', 'seller_name',
            'amount_cents', 'sale_date', 'status',
            'allocated_at',
        ]
        read_only_fields = [
            'uuid', 'amount_cents', 'sale_date', 'status',
            'allocated_at',
        ]


class AllocationCreateSerializer(serializers.Serializer):
    boleto_uuid = serializers.UUIDField()


class CommissionImpactReviewSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(
        source='seller.name', read_only=True
    )
    impact_type_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()

    class Meta:
        model = CommissionImpactReview
        fields = [
            'uuid', 'seller_name', 'impact_type', 'impact_type_display',
            'delta_sale_cents', 'estimated_commission_delta_cents',
            'status', 'status_display', 'reason', 'created_at',
        ]

    def get_impact_type_display(self, obj):
        return obj.get_impact_type_display()

    def get_status_display(self, obj):
        return obj.get_status_display()


from django.utils import timezone
from datetime import timedelta
