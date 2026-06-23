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

            blocked_statuses = [
                CommissionPeriod.Status.FECHADA,
                CommissionPeriod.Status.PAGA,
                CommissionPeriod.Status.AJUSTADA,
                CommissionPeriod.Status.CANCELADA,
            ]
            period_blocked = CommissionPeriod.objects.filter(
                tenant=user.tenant,
                month=sale_date.month,
                year=sale_date.year,
                status__in=blocked_statuses,
            ).exists()

            if period_blocked and user.role == User.Role.SELLER:
                raise serializers.ValidationError({
                    'sale_date': (
                        'Este periodo ja foi fechado. '
                        'Nao e possivel lancar ou editar vendas para este mes. '
                        'Entre em contato com seu gestor se precisar de um ajuste.'
                    ),
                })

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

    class Meta:
        model = SellerCommission
        fields = [
            'id', 'seller_uuid', 'seller_name',
            'total_sold_amount', 'commission_rate', 'commission_amount',
            'period_status',
            'payment_date', 'paid_at', 'paid_amount',
            'payment_method', 'payment_notes',
            'closed_at', 'adjustments',
        ]
        read_only_fields = [
            'id', 'seller_uuid', 'seller_name', 'total_sold_amount',
            'commission_rate', 'commission_amount', 'period_status',
            'payment_date', 'paid_at', 'paid_amount',
            'payment_method', 'payment_notes',
            'closed_at', 'adjustments',
        ]


class CommissionPeriodSerializer(serializers.ModelSerializer):
    seller_commissions = serializers.SerializerMethodField()
    is_current_month = serializers.SerializerMethodField()

    class Meta:
        model = CommissionPeriod
        fields = [
            'uuid', 'tenant', 'month', 'year', 'status',
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
        from app.apps.commissions.services import get_manual_sales_total, get_commission_rate

        commissions = obj.seller_commissions.select_related('seller').all()
        if obj.status == CommissionPeriod.Status.ABERTA:
            import copy
            result = []
            for sc in commissions:
                temp = copy.copy(sc)
                temp.recalculate(commit=False)
                result.append(temp)
            return SellerCommissionReadSerializer(result, many=True).data

        import copy
        result = []
        for sc in commissions:
            has_stale_data = (
                sc.total_sold_amount == 0 and sc.commission_amount == 0
                and get_manual_sales_total(sc.seller, obj.month, obj.year) > 0
            )
            if has_stale_data:
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
        fields = ['month', 'year']

    def validate_month(self, value):
        if value < 1 or value > 12:
            raise serializers.ValidationError('Mes deve estar entre 1 e 12.')
        return value

    def validate_year(self, value):
        if value < 2000 or value > 2100:
            raise serializers.ValidationError('Ano invalido.')
        return value


def slugify(value):
    from django.utils.text import slugify as _slugify
    return _slugify(value)


def get_random_string(length):
    from django.utils.crypto import get_random_string as _get_random_string
    return _get_random_string(length)
