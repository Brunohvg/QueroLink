import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.urls import reverse

from app.apps.accounts.models import Tenant
from app.apps.accounts.plans import OFFERED_PLAN_CODES
from app.apps.billing.models import Subscription, plan_amount
from app.apps.billing.serializers import UpgradeSerializer
from app.apps.api.permissions import IsManagerOrAdmin
from app.services.gateway.mercadopago import MercadoPagoGateway

logger = logging.getLogger(__name__)


def retry_pending_subscription_cancel(subscription):
    pending_id = subscription.pending_cancel_gateway_subscription_id
    if not pending_id:
        return False
    gateway = MercadoPagoGateway()
    gateway.cancel_preapproval(pending_id)
    subscription.pending_cancel_gateway_subscription_id = None
    subscription.save(update_fields=['pending_cancel_gateway_subscription_id', 'updated_at'])
    return True


class PlanListView(APIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        prices = getattr(settings, 'PLAN_PRICES', {})
        limits = getattr(settings, 'PLAN_SELLER_LIMITS', {})
        plan_names = dict(Tenant.Plan.choices)
        data = []
        for key in OFFERED_PLAN_CODES:
            monthly = prices.get(key, 0)
            yearly = plan_amount(key, 'YEARLY') if monthly else 0
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

        if not tenant.billing_email:
            return Response(
                {'error': 'Configure um email de cobranca nas configuracoes da loja.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount = plan_amount(plan, billing_cycle)
        if amount <= 0:
            return Response(
                {'error': 'Plano sem preco configurado. Entre em contato com o suporte.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gateway = MercadoPagoGateway()
        try:
            back_url = request.build_absolute_uri(
                reverse('dashboard:assinatura'),
            )
            frequency = 12 if billing_cycle == 'YEARLY' else 1
            result = gateway.create_preapproval(
                reason=f"Plano {dict(Tenant.Plan.choices)[plan]} - Mérito by Vidalys",
                external_reference=str(tenant.uuid),
                payer_email=tenant.billing_email,
                amount=amount / 100,
                frequency=frequency,
                frequency_type='months',
                back_url=back_url,
            )
        except Exception as e:
            logger.exception("Erro ao criar preapproval no Mercado Pago")
            return Response(
                {'error': f'Erro ao criar assinatura: {e}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        new_gateway_id = (result or {}).get('id')
        if not new_gateway_id:
            logger.error('Mercado Pago retornou preapproval sem id para tenant %s', tenant.uuid)
            return Response(
                {'error': 'Mercado Pago nao retornou identificador da assinatura.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        with transaction.atomic():
            sub, created = Subscription.objects.select_for_update().get_or_create(
                tenant=tenant,
                defaults={
                    'plan': plan,
                    'billing_cycle': billing_cycle,
                    'amount': amount,
                },
            )
            old_gateway_id = sub.gateway_subscription_id
            pending_cancel_id = old_gateway_id if old_gateway_id and old_gateway_id != new_gateway_id else None
            sub.plan = plan
            sub.billing_cycle = billing_cycle
            sub.amount = amount
            sub.gateway_subscription_id = new_gateway_id
            sub.pending_cancel_gateway_subscription_id = pending_cancel_id
            sub.status = Subscription.Status.PENDING
            sub.save(update_fields=[
                'plan', 'billing_cycle', 'amount', 'gateway_subscription_id',
                'pending_cancel_gateway_subscription_id', 'status', 'updated_at',
            ])

        cleanup_pending = False
        if pending_cancel_id:
            try:
                gateway.cancel_preapproval(pending_cancel_id)
                with transaction.atomic():
                    locked = Subscription.objects.select_for_update().get(pk=sub.pk)
                    if locked.pending_cancel_gateway_subscription_id == pending_cancel_id:
                        locked.pending_cancel_gateway_subscription_id = None
                        locked.save(update_fields=['pending_cancel_gateway_subscription_id', 'updated_at'])
            except Exception:
                cleanup_pending = True
                logger.exception('Erro ao cancelar subscription anterior no Mercado Pago')

        cache.delete(f'tenant_operational:{tenant.uuid}')

        init_point = result.get('init_point', '')
        response_status = 'cleanup_pending' if cleanup_pending else sub.status
        return Response({
            'subscription_id': sub.uuid,
            'gateway_id': new_gateway_id,
            'init_point': init_point,
            'status': response_status,
            'cleanup_pending': cleanup_pending,
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
            except Exception:
                logger.exception('Erro ao cancelar assinatura no Mercado Pago')
                return Response(
                    {'error': 'Nao foi possivel confirmar o cancelamento no Mercado Pago.'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        sub.status = Subscription.Status.CANCELED
        sub.save(update_fields=['status', 'updated_at'])

        cache.delete(f'tenant_operational:{tenant.uuid}')

        return Response({'status': 'canceled', 'message': 'Assinatura cancelada.'})
