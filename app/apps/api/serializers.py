from rest_framework import serializers
from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.accounts.models import User
from django.utils.crypto import get_random_string
from django.utils.text import slugify


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
            'sale_date', 'notes', 'created_at',
        ]
        read_only_fields = ['uuid', 'seller_uuid', 'seller_name', 'created_at']


class SaleCreateSerializer(SaleSerializer):
    seller = serializers.PrimaryKeyRelatedField(
        queryset=Seller.objects.all(), required=False
    )

    class Meta(SaleSerializer.Meta):
        fields = ['seller', 'origin', 'amount', 'sale_date', 'notes']

    def validate(self, attrs):
        request = self.context.get('request')
        user = request.user

        if user.role == User.Role.SELLER:
            attrs['seller'] = user.seller_profile

        seller = attrs.get('seller')
        if not seller:
            raise serializers.ValidationError({'seller': 'Vendedor e obrigatorio.'})
        if seller.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                {'seller': 'O vendedor nao pertence ao tenant.'}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['tenant'] = request.user.tenant
        validated_data['created_by'] = request.user
        return super().create(validated_data)


class CommissionPeriodSerializer(serializers.ModelSerializer):
    seller_commissions = serializers.SerializerMethodField()

    class Meta:
        model = CommissionPeriod
        fields = [
            'uuid', 'tenant', 'month', 'year', 'status',
            'sent_to_financial_at', 'approved_at', 'paid_at',
            'created_at', 'seller_commissions',
        ]
        read_only_fields = [
            'uuid', 'tenant', 'sent_to_financial_at', 'approved_at',
            'paid_at', 'created_at', 'seller_commissions',
        ]

    def get_seller_commissions(self, obj):
        return SellerCommissionReadSerializer(
            obj.seller_commissions.select_related('seller').all(),
            many=True,
        ).data


class SellerCommissionReadSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    seller_uuid = serializers.CharField(source='seller.uuid', read_only=True)

    class Meta:
        model = SellerCommission
        fields = [
            'id', 'seller_uuid', 'seller_name',
            'total_sold_amount', 'commission_rate', 'commission_amount',
            'payment_date', 'payment_method', 'payment_notes',
        ]
        read_only_fields = [
            'id', 'seller_uuid', 'seller_name', 'total_sold_amount',
            'commission_rate', 'commission_amount',
            'payment_date', 'payment_method', 'payment_notes',
        ]


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
