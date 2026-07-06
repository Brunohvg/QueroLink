from rest_framework import serializers
from django.conf import settings
from app.apps.accounts.models import Tenant
from app.apps.accounts.plans import offered_plan_choices


class UpgradeSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=offered_plan_choices())
    billing_cycle = serializers.ChoiceField(
        choices=[('MONTHLY', 'Mensal'), ('YEARLY', 'Anual')],
    )

    def validate_plan(self, value):
        limits = getattr(settings, 'PLAN_SELLER_LIMITS', {})
        limit = limits.get(value)
        tenant = self.context['request'].user.tenant
        from app.apps.sellers.models import Seller
        current = Seller.objects.filter(tenant=tenant, is_active=True).count()
        if limit is not None and current > limit:
            raise serializers.ValidationError(
                f'Seu plano atual tem {current} vendedores ativos, '
                f'mas o plano {dict(Tenant.Plan.choices)[value]} '
                f'permite no maximo {limit}. Remova vendedores antes de fazer downgrade.'
            )
        return value
