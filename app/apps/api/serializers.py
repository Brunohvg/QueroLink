from datetime import date

from rest_framework import serializers
from django.utils import timezone

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.accounts.models import User


class SellerSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Seller
        fields = [
            'uuid', 'name', 'phone', 'commission_rate',
            'is_active', 'username', 'created_at',
        ]
        read_only_fields = ['uuid', 'username', 'created_at']


class SellerCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)

    def create(self, validated_data):
        request = self.context['request']
        tenant = request.user.tenant

        base = slugify(validated_data['name'])
        existing = set(User.objects.values_list('username', flat=True))
        username = base
        n = 2
        while username in existing:
            username = f"{base}-{n}"
            n += 1

        password = get_random_string(12)

        user = User.objects.create_user(
            username=username,
            password=password,
            role=User.Role.SELLER,
            tenant=tenant,
        )

        seller = Seller.objects.create(
            tenant=tenant,
            user=user,
            name=validated_data['name'],
            phone=validated_data['phone'],
            commission_rate=tenant.default_commission_rate,
        )

        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
        except Exception:
            pass

        return {
            'uuid': str(seller.uuid),
            'name': seller.name,
            'phone': seller.phone,
            'username': username,
            'password': password,
        }


class SaleSerializer(serializers.ModelSerializer):
    seller_uuid = serializers.CharField(source='seller.uuid', read_only=True)
    seller_name = serializers.CharField(source='seller.name', read_only=True)

    class Meta:
        model = Sale
        fields = [
            'uuid', 'seller_uuid', 'seller_name', 'origin', 'amount',
            'sale_date', 'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'uuid', 'seller_uuid', 'seller_name', 'created_at', 'updated_at',
        ]


class SaleCreateSerializer(serializers.ModelSerializer):
    seller = serializers.PrimaryKeyRelatedField(
        queryset=Seller.objects.all(), required=False,
    )

    class Meta:
        model = Sale
        fields = ['seller', 'origin', 'amount', 'sale_date', 'notes']

    def get_validators(self):
        return []

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user
        today = timezone.localdate()

        if user.role == User.Role.SELLER:
            attrs['seller'] = user.seller_profile

        seller = attrs.get('seller')
        if not seller:
            raise serializers.ValidationError({'seller': 'Vendedor e obrigatorio.'})
        if seller.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                {'seller': 'O vendedor nao pertence ao tenant.'}
            )

        sale_date = attrs.get('sale_date')
        if not sale_date:
            raise serializers.ValidationError(
                {'sale_date': 'Data do lancamento e obrigatoria.'}
            )

        if sale_date > today:
            raise serializers.ValidationError({
                'sale_date': 'Nao e possivel lancar vendas em data futura.'
            })

        origin = attrs.get('origin', Sale.Origin.MANUAL)

        if origin == Sale.Origin.MANUAL:
            if user.role == User.Role.SELLER:
                if sale_date.year < today.year or (
                    sale_date.year == today.year
                    and sale_date.month < today.month
                ):
                    raise serializers.ValidationError({
                        'sale_date': (
                            'Nao e possivel lancar ou editar vendas de meses '
                            'anteriores. Entre em contato com seu gestor.'
                        ),
                    })

            from app.apps.commissions.services import validate_sale_can_be_changed
            can_change, error_msg = validate_sale_can_be_changed(seller, sale_date, user)
            if not can_change:
                raise serializers.ValidationError({'sale_date': error_msg})

            existing = Sale.objects.filter(
                seller=seller,
                sale_date=sale_date,
                origin=Sale.Origin.MANUAL,
            )
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise serializers.ValidationError({
                    'sale_date': (
                        'Ja existe um lancamento manual para este vendedor '
                        'nesta data. Para alterar, edite o lancamento existente.'
                    ),
                })

        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['tenant'] = request.user.tenant
        validated_data['created_by'] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        validated_data['updated_by'] = request.user
        return super().update(instance, validated_data)


class CommissionAdjustmentSerializer(serializers.ModelSerializer):
    adjusted_by_name = serializers.CharField(
        source='adjusted_by.username', read_only=True,
    )

    class Meta:
        model = CommissionAdjustment
        fields = [
            'id', 'previous_amount', 'new_amount', 'difference',
            'reason', 'adjusted_by_name', 'created_at',
        ]
        read_only_fields = [
            'id', 'previous_amount', 'new_amount', 'difference',
            'adjusted_by_name', 'created_at',
        ]


class SellerCommissionReadSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    seller_uuid = serializers.CharField(source='seller.uuid', read_only=True)
    period_status = serializers.CharField(
        source='period.status', read_only=True,
    )
    adjustments = CommissionAdjustmentSerializer(many=True, read_only=True)
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = SellerCommission
        fields = [
            'id', 'seller_uuid', 'seller_name',
            'status', 'operational_status',
            'total_sold_amount', 'commission_rate', 'commission_amount',
            'expected_working_days', 'submitted_days_count', 'missing_days_count',
            'frozen_total_sold_amount', 'frozen_commission_rate',
            'frozen_commission_amount',
            'period_status',
            'closed_at', 'reopened_at', 'reopen_reason',
            'paid_at', 'paid_amount', 'payment_date',
            'payment_method', 'payment_notes',
            'adjustments', 'is_editable',
        ]
        read_only_fields = fields


class CommissionPeriodSerializer(serializers.ModelSerializer):
    seller_commissions = serializers.SerializerMethodField()
    is_current_month = serializers.SerializerMethodField()

    class Meta:
        model = CommissionPeriod
        fields = [
            'uuid', 'tenant', 'month', 'year', 'status',
            'expected_working_days', 'notes',
            'closed_at', 'paid_at', 'adjusted_at',
            'adjustment_reason', 'cancelled_at', 'cancel_reason',
            'created_at', 'seller_commissions', 'is_current_month',
        ]
        read_only_fields = [
            'uuid', 'tenant', 'closed_at', 'paid_at', 'adjusted_at',
            'adjustment_reason', 'cancelled_at', 'cancel_reason',
            'created_at', 'seller_commissions', 'is_current_month',
        ]

    def get_seller_commissions(self, obj):
        commissions = obj.seller_commissions.select_related('seller').all()
        import copy
        result = []
        for sc in commissions:
            if sc.is_editable:
                temp = copy.copy(sc)
                temp.recalculate(commit=False)
                result.append(temp)
            else:
                result.append(sc)
        return SellerCommissionReadSerializer(result, many=True).data

    def get_is_current_month(self, obj):
        hoje = timezone.localdate()
        return obj.month == hoje.month and obj.year == hoje.year


class CommissionPeriodCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionPeriod
        fields = ['month', 'year', 'expected_working_days', 'notes']
        extra_kwargs = {
            'expected_working_days': {'required': False},
            'notes': {'required': False},
        }

    def validate_month(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError('Mes deve estar entre 1 e 12.')
        return value

    def validate_year(self, value):
        if value < 2000 or value > 2100:
            raise serializers.ValidationError('Ano invalido.')
        return value

    def validate(self, attrs):
        request = self.context.get('request')
        tenant = request.user.tenant
        month = attrs.get('month')
        year = attrs.get('year')
        if CommissionPeriod.objects.filter(
            tenant=tenant, month=month, year=year,
        ).exists():
            raise serializers.ValidationError(
                f'A competencia {month:02d}/{year} ja existe. '
                'Use Atualizar valores para sincronizar os dados.'
            )
        return attrs


def slugify(value):
    from django.utils.text import slugify as _slugify
    return _slugify(value)


def get_random_string(length):
    from django.utils.crypto import get_random_string as _get_random_string
    return _get_random_string(length)
