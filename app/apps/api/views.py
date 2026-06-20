from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from rest_framework_simplejwt.views import TokenObtainPairView

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.accounts.models import User
from app.apps.audit.models import AuditLog

from .serializers import (
    SellerSerializer,
    SellerCreateSerializer,
    SaleSerializer,
    SaleCreateSerializer,
    CommissionPeriodSerializer,
    CommissionPeriodCreateSerializer,
)
from .permissions import IsManagerOrAdmin, IsFinancialOrAdmin, IsSellerOwner


def _log_audit(user, action, model_name, object_id, changes=None, request=None):
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        changes=changes or {},
        ip_address=request.META.get('REMOTE_ADDR') if request else None,
    )


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

    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        seller = self.get_object()
        if not seller.user:
            return Response({'error': 'Vendedor sem usuario vinculado.'}, status=400)
        from django.utils.crypto import get_random_string
        password = get_random_string(12)
        seller.user.set_password(password)
        seller.user.save()
        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
        except Exception:
            pass
        _log_audit(request.user, 'reset_password', 'Seller', str(seller.uuid), request=request)
        return Response({'password': password, 'username': seller.user.username})


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
        _log_audit(request.user, 'approve', 'CommissionPeriod', str(period.uuid),
                   {'month': period.month, 'year': period.year}, request)
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
        reason = request.data.get('reason', '')
        period.status = CommissionPeriod.Status.EM_CONFERENCIA
        period.sent_to_financial_at = None
        period.save(update_fields=['status', 'sent_to_financial_at', 'updated_at'])
        _log_audit(request.user, 'reject', 'CommissionPeriod', str(period.uuid),
                   {'month': period.month, 'year': period.year, 'reason': reason}, request)
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


class RankingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        tenant = request.user.tenant
        month = int(request.query_params.get('month', timezone.localdate().month))
        year = int(request.query_params.get('year', timezone.localdate().year))

        sales = Sale.objects.filter(
            tenant=tenant,
            sale_date__year=year,
            sale_date__month=month,
        ).values('seller__uuid', 'seller__name').annotate(
            total_sold=Sum('amount'),
            sale_count=Sum(1),
        ).order_by('-total_sold')

        top_seller = sales[0] if sales else None

        return Response({
            'month': month,
            'year': year,
            'ranking': list(sales),
            'top_seller': top_seller,
        })


class CommissionPeriodsByStatusView(generics.ListAPIView):
    serializer_class = CommissionPeriodSerializer
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        tenant = self.request.user.tenant
        status_filter = self.kwargs.get('status', 'ABERTA')
        return CommissionPeriod.objects.filter(
            tenant=tenant, status=status_filter
        ).prefetch_related('seller_commissions__seller').order_by('-year', '-month')


class CommissionPeriodCsvView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsFinancialOrAdmin]

    def get(self, request, pk=None):
        import csv, io
        period = CommissionPeriod.objects.get(uuid=pk, tenant=request.user.tenant)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['Vendedor', 'Total Vendido (R$)', 'Taxa (%)', 'Comissao (R$)'])
        for sc in period.seller_commissions.select_related('seller').all():
            writer.writerow([
                sc.seller.name,
                f'{sc.total_sold_amount / 100:.2f}',
                f'{float(sc.commission_rate) * 100:.2f}',
                f'{sc.commission_amount / 100:.2f}',
            ])
        response = Response(buf.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="comissao_{period.month}_{period.year}.csv"'
        return response


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
