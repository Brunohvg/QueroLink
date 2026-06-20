from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from rest_framework_simplejwt.views import TokenObtainPairView

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.accounts.models import User

from .serializers import (
    SellerSerializer,
    SellerCreateSerializer,
    SaleSerializer,
    SaleCreateSerializer,
    CommissionPeriodSerializer,
    CommissionPeriodCreateSerializer,
)
from .permissions import IsManagerOrAdmin, IsFinancialOrAdmin, IsSellerOwner


class SellerViewSet(viewsets.ModelViewSet):
    queryset = Seller.objects.select_related('user').all()
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return SellerCreateSerializer
        return SellerSerializer

    def get_queryset(self):
        return Seller.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('user')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=status.HTTP_201_CREATED)


class SaleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsSellerOwner | IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SaleCreateSerializer
        return SaleSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Sale.objects.select_related('seller', 'order').filter(tenant=user.tenant)
        if user.role == User.Role.SELLER:
            qs = qs.filter(seller=user.seller_profile)
        return qs

    def perform_create(self, serializer):
        serializer.save()


class CommissionPeriodViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return CommissionPeriodCreateSerializer
        return CommissionPeriodSerializer

    def get_queryset(self):
        return CommissionPeriod.objects.filter(
            tenant=self.request.user.tenant
        ).prefetch_related('seller_commissions__seller')

    def perform_create(self, serializer):
        tenant = self.request.user.tenant
        period = serializer.save(tenant=tenant)

        for seller in Seller.objects.filter(tenant=tenant, is_active=True):
            SellerCommission.objects.get_or_create(
                period=period,
                seller=seller,
                defaults={'commission_rate': seller.commission_rate},
            )

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Fecha a competencia e recalcula comissoes de cada vendedor."""
        period = self.get_object()
        if period.status != CommissionPeriod.Status.ABERTA:
            return Response(
                {'error': f'Nao e possivel fechar competencia com status {period.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for sc in period.seller_commissions.select_related('seller').all():
            sc.recalculate()
            sc.save(update_fields=['total_sold_amount', 'commission_amount'])

        period.status = CommissionPeriod.Status.EM_CONFERENCIA
        period.save(update_fields=['status', 'updated_at'])

        serializer = self.get_serializer(period)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """Envia a competencia para o financeiro."""
        period = self.get_object()
        if period.status != CommissionPeriod.Status.EM_CONFERENCIA:
            return Response(
                {'error': f'Nao e possivel enviar competencia com status {period.status}. O status deve ser EM_CONFERENCIA.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period.status = CommissionPeriod.Status.ENVIADA_FINANCEIRO
        period.sent_to_financial_at = timezone.now()
        period.save(update_fields=['status', 'sent_to_financial_at', 'updated_at'])
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsFinancialOrAdmin])
    def approve(self, request, pk=None):
        """Financeiro aprova a competencia para pagamento."""
        period = self.get_object()
        if period.status != CommissionPeriod.Status.ENVIADA_FINANCEIRO:
            return Response(
                {'error': f'Nao e possivel aprovar competencia com status {period.status}. O status deve ser ENVIADA_FINANCEIRO.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period.status = CommissionPeriod.Status.APROVADA
        period.approved_at = timezone.now()
        period.save(update_fields=['status', 'approved_at', 'updated_at'])
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsFinancialOrAdmin])
    def reject(self, request, pk=None):
        """Financeiro rejeita e devolve para conferencia."""
        period = self.get_object()
        if period.status != CommissionPeriod.Status.ENVIADA_FINANCEIRO:
            return Response(
                {'error': f'Nao e possivel rejeitar competencia com status {period.status}. O status deve ser ENVIADA_FINANCEIRO.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period.status = CommissionPeriod.Status.EM_CONFERENCIA
        period.sent_to_financial_at = None
        period.save(update_fields=['status', 'sent_to_financial_at', 'updated_at'])
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsFinancialOrAdmin])
    def mark_paid(self, request, pk=None):
        """Financeiro marca competencia como paga e dispara notificacoes."""
        period = self.get_object()
        if period.status != CommissionPeriod.Status.APROVADA:
            return Response(
                {'error': f'Nao e possivel marcar como paga competencia com status {period.status}. O status deve ser APROVADA.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period.status = CommissionPeriod.Status.PAGA
        period.paid_at = timezone.now()
        period.save(update_fields=['status', 'paid_at', 'updated_at'])

        from app.apps.notifications.tasks import notify_commission_paid
        for sc in period.seller_commissions.select_related('seller').all():
            try:
                notify_commission_paid(sc)
            except Exception:
                pass

        return Response(self.get_serializer(period).data)


class JWTLoginView(TokenObtainPairView):
    @method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True))
    @method_decorator(ratelimit(key='post:username', rate='5/m', method='POST', block=True))
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class SellerSalesListView(generics.ListAPIView):
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated, IsSellerOwner]

    def get_queryset(self):
        try:
            seller = self.request.user.seller_profile
        except Exception:
            return Sale.objects.none()
        return Sale.objects.filter(seller=seller, tenant=self.request.user.tenant)


class ManagerSalesListView(generics.ListAPIView):
    serializer_class = SaleSerializer
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        tenant = self.request.user.tenant
        qs = Sale.objects.select_related('seller').filter(tenant=tenant)
        seller_uuid = self.request.query_params.get('seller')
        if seller_uuid:
            qs = qs.filter(seller__uuid=seller_uuid)
        return qs
