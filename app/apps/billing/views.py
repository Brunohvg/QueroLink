import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings
from django.urls import reverse

from app.apps.accounts.models import Tenant
from app.apps.billing.models import Subscription
from app.apps.billing.serializers import UpgradeSerializer
from app.apps.api.permissions import IsManagerOrAdmin
from app.services.gateway.mercadopago import MercadoPagoGateway, MercadoPagoError

logger = logging.getLogger(__name__)


class PlanListView(APIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        prices = getattr(settings, 'PLAN_PRICES', {})
        limits = getattr(settings, 'PLAN_SELLER_LIMITS', {})
        plan_names = dict(Tenant.Plan.choices)
        data = []
        for key in ['ESSENCIAL', 'PROFISSIONAL', 'PLUS', 'ENTERPRISE']:
            monthly = prices.get(key, 0)
            yearly = int(monthly * 12 * 0.9) if monthly else 0
            data.append({
                'id': key,
                'name': plan_names.get(key, key),
                'seller_limit': limits.get(key),
                'price_monthly': monthly,
                'price_yearly': yearly,
            })
        return Response(data)


class UpgradeSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def post(self, request):
        serializer = UpgradeSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)

        tenant = request.user.tenant
        plan = serializer.validated_data['plan']
        billing_cycle = serializer.validated_data['billing_cycle']
        payment_method = serializer.validated_data['payment_method']

        if not tenant.billing_email:
            return Response(
                {'error': 'Configure um email de cobranca nas configuracoes da loja.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        prices = getattr(settings, 'PLAN_PRICES', {})
        amount = prices.get(plan, 0)
        if billing_cycle == 'YEARLY':
            amount = int(amount * 12 * 0.9)

        if amount <= 0:
            return Response(
                {'error': 'Plano sem preco configurado. Entre em contato com o suporte.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sub, created = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                'plan': plan,
                'billing_cycle': billing_cycle,
                'amount': amount,
            },
        )

        if sub.gateway_subscription_id:
            try:
                gateway = MercadoPagoGateway()
                gateway.cancel_preapproval(sub.gateway_subscription_id)
            except (MercadoPagoError, Exception) as e:
                logger.warning(
                    "Erro ao cancelar subscription anterior %s: %s",
                    sub.gateway_subscription_id, e,
                )

        try:
            gateway = MercadoPagoGateway()
            back_url = request.build_absolute_uri(
                reverse('dashboard:assinatura'),
            )
            result = gateway.create_preapproval(
                reason=f"Plano {dict(Tenant.Plan.choices)[plan]} - V-Com",
                external_reference=str(tenant.uuid),
                payer_email=tenant.billing_email,
                amount=amount / 100,
                back_url=back_url,
            )
        except (MercadoPagoError, Exception) as e:
            logger.exception("Erro ao criar preapproval no Mercado Pago")
            return Response(
                {'error': f'Erro ao criar assinatura: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        sub.plan = plan
        sub.billing_cycle = billing_cycle
        sub.amount = amount
        sub.gateway_subscription_id = result.get('id')
        sub.status = Subscription.Status.TRIALING
        sub.save()

        tenant.plan = plan
        tenant.save(update_fields=['plan', 'updated_at'])

        init_point = result.get('init_point', '')
        return Response({
            'subscription_id': sub.uuid,
            'gateway_id': result.get('id'),
            'init_point': init_point,
            'status': sub.status,
        })


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def post(self, request):
        tenant = request.user.tenant
        try:
            sub = Subscription.objects.get(tenant=tenant)
        except Subscription.DoesNotExist:
            return Response(
                {'error': 'Nenhuma assinatura encontrada.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if sub.status == Subscription.Status.CANCELED:
            return Response(
                {'error': 'Assinatura ja cancelada.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if sub.gateway_subscription_id:
            try:
                gateway = MercadoPagoGateway()
                gateway.cancel_preapproval(sub.gateway_subscription_id)
            except (MercadoPagoError, Exception) as e:
                logger.warning(
                    "Erro ao cancelar no MP: %s", e,
                )

        sub.status = Subscription.Status.CANCELED
        sub.save(update_fields=['status', 'updated_at'])

        return Response({'status': 'canceled', 'message': 'Assinatura cancelada.'})
