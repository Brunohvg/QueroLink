from datetime import date, timedelta

from rest_framework import viewsets, status, generics, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponse
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from rest_framework_simplejwt.views import TokenObtainPairView

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.accounts.models import User

from .serializers import (
    SellerSerializer,
    SellerCreateSerializer,
    SellerImportSerializer,
    SaleSerializer,
    SaleCreateSerializer,
    CommissionPeriodSerializer,
    CommissionPeriodCreateSerializer,
    ChangePasswordSerializer,
)
from .permissions import IsManagerOrAdmin, IsFinancialOrAdmin, IsSellerOwner
from app.apps.audit.utils import log_action


class SellerViewSet(viewsets.ModelViewSet):
    queryset = Seller.objects.select_related('user').all()
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return SellerCreateSerializer
        return SellerSerializer

    def get_queryset(self):
        return Seller.objects.filter(
            tenant=self.request.user.tenant,
        ).select_related('user')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        seller = self.get_queryset().get(uuid=result['uuid'])
        log_action(request, 'seller.created', instance=seller)
        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        seller = self.get_object()
        if not seller.user:
            return Response(
                {'error': 'Vendedor sem usuario vinculado.'}, status=400,
            )
        from django.utils.crypto import get_random_string
        password = get_random_string(12)
        seller.user.set_password(password)
        seller.user.save()
        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
        except Exception:
            pass
        log_action(request, 'seller.password_reset', instance=seller)
        return Response({'message': 'Senha redefinida com sucesso.'})

    @action(detail=False, methods=['post'])
    def import_sellers(self, request):
        serializer = SellerImportSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        log_action(request, 'seller.import', changes={
            'total': result['total'],
            'criados': result['criados'],
            'erros': result['erros'],
        })
        return Response(result, status=status.HTTP_200_OK)


class SaleViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsSellerOwner | IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return SaleCreateSerializer
        return SaleSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Sale.objects.select_related('seller', 'order').filter(
            tenant=user.tenant,
        )
        if user.role == User.Role.SELLER:
            qs = qs.filter(seller=user.seller_profile)
        return qs

    def perform_create(self, serializer):
        sale = serializer.save()
        log_action(self.request, 'sale.created', instance=sale)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
        log_action(self.request, 'sale.updated', instance=serializer.instance)

    def perform_destroy(self, instance):
        from app.apps.commissions.services import validate_sale_can_be_changed
        can_change, error_msg = validate_sale_can_be_changed(
            instance.seller, instance.sale_date, self.request.user,
        )
        if not can_change:
            raise drf_serializers.ValidationError({'detail': error_msg})
        log_action(self.request, 'sale.deleted', instance=instance)
        instance.delete()


class CommissionPeriodViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get_serializer_class(self):
        if self.action == 'create':
            return CommissionPeriodCreateSerializer
        return CommissionPeriodSerializer

    def get_queryset(self):
        return CommissionPeriod.objects.filter(
            tenant=self.request.user.tenant,
        ).prefetch_related('seller_commissions__seller')

    def perform_create(self, serializer):
        tenant = self.request.user.tenant
        period = serializer.save(tenant=tenant)

        from app.apps.commissions.services import sync_period_seller_commissions
        sync_period_seller_commissions(period)

    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        from app.apps.commissions.services import sync_period_seller_commissions
        period = self.get_object()
        created = sync_period_seller_commissions(period)
        return Response({
            'synced': True,
            'created': created,
            'message': 'Competencia sincronizada com sucesso.' if created > 0
                       else 'Competencia ja estava sincronizada.',
        })

    @action(detail=True, methods=['post'])
    @method_decorator(ratelimit(key='user', rate='30/h', method='POST', block=True))
    def close_sellers(self, request, pk=None):
        from app.apps.commissions.services import close_seller_commissions

        period = self.get_object()
        seller_ids = request.data.get('seller_commission_ids', [])

        if not seller_ids:
            return Response(
                {'error': 'Selecione ao menos um vendedor.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            calculations = close_seller_commissions(period, seller_ids, request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        log_action(
            request, 'commission_period.close_sellers', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'seller_ids': seller_ids,
                'closed_count': len(calculations),
            },
        )
        return Response({
            'closed': len(calculations),
            'commissions': calculations,
        })

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    @method_decorator(ratelimit(key='user', rate='30/h', method='POST', block=True))
    def reopen_sellers(self, request, pk=None):
        from app.apps.commissions.services import reopen_seller_commissions

        period = self.get_object()
        seller_ids = request.data.get('seller_commission_ids', [])
        reason = request.data.get('reason', '').strip()

        if not seller_ids:
            return Response(
                {'error': 'Selecione ao menos um vendedor.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            commissions = reopen_seller_commissions(period, seller_ids, request.user, reason)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        log_action(
            request, 'commission_period.reopen_sellers', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'seller_ids': seller_ids,
                'reason': reason,
            },
        )
        return Response({
            'reopened': len(commissions),
            'message': f'{len(commissions)} vendedor(es) reaberto(s).',
        })

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsFinancialOrAdmin],
    )
    @method_decorator(ratelimit(key='user', rate='30/h', method='POST', block=True))
    def pay_sellers(self, request, pk=None):
        from app.apps.commissions.services import pay_seller_commissions

        period = self.get_object()
        seller_ids = request.data.get('seller_commission_ids', [])

        if not seller_ids:
            return Response(
                {'error': 'Selecione ao menos um vendedor.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_data = {
            'payment_date': request.data.get('payment_date'),
            'payment_method': request.data.get('payment_method', ''),
            'payment_notes': request.data.get('payment_notes', ''),
        }

        try:
            commissions = pay_seller_commissions(period, seller_ids, request.user, payment_data)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        log_action(
            request, 'commission_period.pay_sellers', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'seller_ids': seller_ids,
                'payment_date': payment_data.get('payment_date'),
            },
        )
        return Response({
            'paid': len(commissions),
            'message': f'{len(commissions)} vendedor(es) pago(s).',
        })

    def update(self, request, *args, **kwargs):
        from app.apps.commissions.services import update_period

        partial = kwargs.pop('partial', False)
        period = self.get_object()
        data = request.data

        try:
            period, changed = update_period(period, data, request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        log_action(
            request, 'commission_period.updated', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'changed_fields': changed,
                'new_data': dict(data),
            },
        )
        return Response(self.get_serializer(period).data)

    def perform_destroy(self, instance):
        from app.apps.commissions.services import delete_period

        try:
            sc_count = delete_period(instance, self.request.user)
        except ValueError as e:
            from rest_framework import serializers as drf_ser
            raise drf_ser.ValidationError({'detail': str(e)})

        log_action(
            self.request, 'commission_period.deleted', instance=instance,
            changes={
                'month': instance.month, 'year': instance.year,
                'seller_commissions_removed': sc_count,
            },
        )

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    def cancel(self, request, pk=None):
        from app.apps.commissions.services import cancel_period

        period = self.get_object()
        reason = request.data.get('reason', '').strip()

        try:
            period = cancel_period(period, reason, request.user)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        log_action(
            request, 'commission_period.cancelled', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'reason': reason,
            },
        )
        return Response(self.get_serializer(period).data)


class PaymentQueueView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin]

    def get(self, request):
        tenant = request.user.tenant
        periods = CommissionPeriod.objects.filter(
            tenant=tenant,
        ).exclude(
            status=CommissionPeriod.Status.CANCELADA,
        ).prefetch_related(
            'seller_commissions__seller',
        ).order_by('-year', '-month')

        result = []
        for period in periods:
            fechadas = [
                sc for sc in period.seller_commissions.all()
                if sc.status == SellerCommission.Status.FECHADA
            ]
            if not fechadas:
                continue

            from .serializers import SellerCommissionReadSerializer
            result.append({
                'period': {
                    'uuid': str(period.uuid),
                    'month': period.month,
                    'year': period.year,
                    'status': period.status,
                },
                'commissions': SellerCommissionReadSerializer(fechadas, many=True).data,
            })

        return Response(result)


class JWTLoginView(TokenObtainPairView):
    @method_decorator(ratelimit(key='ip', rate='5/m', method='POST', block=True))
    @method_decorator(
        ratelimit(key='post:username', rate='5/m', method='POST', block=True),
    )
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
        return Sale.objects.filter(
            seller=seller, tenant=self.request.user.tenant,
        )


class RankingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        from app.apps.commissions.services import (
            calculate_estimated_commission, get_commission_rate,
        )
        from decimal import Decimal, ROUND_HALF_UP

        tenant = request.user.tenant
        month = int(request.query_params.get(
            'month', timezone.localdate().month,
        ))
        year = int(request.query_params.get(
            'year', timezone.localdate().year,
        ))
        if month < 1 or month > 12:
            return Response({'error': 'Mes invalido (1-12).'}, status=400)
        if year < 2020:
            return Response({'error': 'Ano invalido.'}, status=400)

        sales = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            sale_date__year=year,
            sale_date__month=month,
        ).values('seller__uuid', 'seller__name', 'seller__commission_rate').annotate(
            total_sold=Sum('amount'),
            sale_count=Sum(1),
        ).order_by('-total_sold')

        ranking = []
        seller_uuids = [s['seller__uuid'] for s in sales]
        sellers_map = {
            str(s.uuid): s
            for s in Seller.objects.filter(uuid__in=seller_uuids, tenant=tenant)
        }

        for s in sales:
            seller_uuid = s['seller__uuid']
            total_sold = s['total_sold']
            sale_count = s['sale_count']
            ticket_medio = round(total_sold / sale_count) if sale_count > 0 else 0

            seller_obj = sellers_map.get(str(seller_uuid))
            if seller_obj:
                rate = get_commission_rate(seller_obj)
                commission_estimada = int((Decimal(str(total_sold)) * Decimal(str(rate))).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            else:
                rate = Decimal('0')
                commission_estimada = 0

            ranking.append({
                'seller__uuid': seller_uuid,
                'seller__name': s['seller__name'],
                'total_sold': total_sold,
                'sale_count': sale_count,
                'ticket_medio': ticket_medio,
                'commission_rate': float(rate),
                'commission_estimada': commission_estimada,
            })

        top_seller = ranking[0] if ranking else None

        return Response({
            'month': month,
            'year': year,
            'ranking': ranking,
            'top_seller': top_seller,
        })


class CommissionPeriodsByStatusView(generics.ListAPIView):
    serializer_class = CommissionPeriodSerializer
    permission_classes = [IsAuthenticated, IsManagerOrAdmin | IsFinancialOrAdmin]

    def get_queryset(self):
        tenant = self.request.user.tenant
        status_filter = self.kwargs.get('status', 'ABERTA')
        return CommissionPeriod.objects.filter(
            tenant=tenant, status=status_filter,
        ).prefetch_related(
            'seller_commissions__seller',
        ).order_by('-year', '-month')


class CommissionPeriodCsvView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin]

    def get(self, request, pk=None):
        import csv
        import io

        period = CommissionPeriod.objects.get(
            uuid=pk, tenant=request.user.tenant,
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            'Vendedor', 'Total Vendido (R$)', 'Taxa (%)',
            'Comissao (R$)', 'Status',
        ])
        for sc in period.seller_commissions.select_related('seller').all():
            writer.writerow([
                sc.seller.name,
                f'{sc.total_sold_amount / 100:.2f}',
                f'{float(sc.commission_rate) * 100:.2f}',
                f'{sc.commission_amount / 100:.2f}',
                period.get_status_display(),
            ])
        response = HttpResponse(
            buf.getvalue(), content_type='text/csv; charset=utf-8',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="comissao_{period.month}_{period.year}.csv"'
        )
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


class SellerDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import calendar

        tenant = request.user.tenant
        try:
            seller = Seller.objects.select_related('user').get(
                uuid=seller_id, tenant=tenant,
            )
        except Seller.DoesNotExist:
            return Response(
                {'error': 'Vendedor nao encontrado.'}, status=404,
            )

        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        hoje = timezone.localdate()

        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end = date(year, month, last_day)

        manual_sales_qs = Sale.objects.filter(
            tenant=tenant, seller=seller,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('-sale_date', '-created_at')

        link_sales_qs = Sale.objects.filter(
            tenant=tenant, seller=seller,
            origin=Sale.Origin.LINK,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('-sale_date', '-created_at')

        manual_total = manual_sales_qs.aggregate(
            t=Sum('amount'),
        )['t'] or 0
        link_total = link_sales_qs.aggregate(
            t=Sum('amount'),
        )['t'] or 0

        manual_sales = []
        for s in manual_sales_qs[:200]:
            manual_sales.append({
                'uuid': str(s.uuid),
                'amount': s.amount,
                'origin': s.origin,
                'origin_display': s.get_origin_display(),
                'sale_date': s.sale_date.isoformat(),
                'notes': s.notes or '',
            })

        link_sales = []
        for s in link_sales_qs[:200]:
            link_sales.append({
                'uuid': str(s.uuid),
                'amount': s.amount,
                'origin': s.origin,
                'origin_display': s.get_origin_display(),
                'sale_date': s.sale_date.isoformat(),
                'notes': s.notes or '',
            })

        commissions = SellerCommission.objects.filter(
            seller=seller,
        ).select_related('period').prefetch_related(
            'adjustments',
        ).order_by('-period__year', '-period__month')

        commissions_data = []
        for sc in commissions:
            adjustments_data = [
                {
                    'previous_amount': a.previous_amount,
                    'new_amount': a.new_amount,
                    'difference': a.difference,
                    'reason': a.reason,
                    'created_at': a.created_at.isoformat(),
                }
                for a in sc.adjustments.all()
            ]

            period_status = sc.period.status
            total_sold = sc.total_sold_amount
            commission_amount = sc.commission_amount

            if period_status == CommissionPeriod.Status.ABERTA:
                from app.apps.commissions.services import calculate_estimated_commission
                est, total = calculate_estimated_commission(
                    seller, sc.period.month, sc.period.year,
                )
                total_sold = total
                commission_amount = est

            commissions_data.append({
                'period_month': sc.period.month,
                'period_year': sc.period.year,
                'period_status': period_status,
                'total_sold_amount': total_sold,
                'commission_rate': float(sc.commission_rate),
                'commission_amount': commission_amount,
                'status': sc.status,
                'operational_status': sc.operational_status,
                'submitted_days_count': sc.submitted_days_count,
                'expected_working_days': sc.expected_working_days,
                'missing_days_count': sc.missing_days_count,
                'closed_at': sc.closed_at.isoformat() if sc.closed_at else None,
                'paid_at': sc.paid_at.isoformat() if sc.paid_at else None,
                'paid_amount': sc.paid_amount,
                'payment_date': sc.payment_date.isoformat() if sc.payment_date else None,
                'payment_method': sc.payment_method,
                'adjustments': adjustments_data,
            })

        evo = manual_sales_qs.values(
            'sale_date',
        ).annotate(
            day_total=Sum('amount'),
        ).order_by('sale_date')
        evolution = [
            {'date': e['sale_date'].isoformat(), 'total': e['day_total']}
            for e in evo
        ]

        comp_data = []
        for m in range(5, -1, -1):
            cm = hoje.month - m
            cy = hoje.year
            if cm <= 0:
                cm += 12
                cy -= 1
            ms = date(cy, cm, 1)
            me = date(cy, cm, calendar.monthrange(cy, cm)[1])
            mt = Sale.objects.filter(
                tenant=tenant, seller=seller,
                origin=Sale.Origin.MANUAL,
                sale_date__gte=ms, sale_date__lte=me,
            ).aggregate(t=Sum('amount'))['t'] or 0
            mc = Sale.objects.filter(
                tenant=tenant, seller=seller,
                origin=Sale.Origin.MANUAL,
                sale_date__gte=ms, sale_date__lte=me,
            ).count()
            sc_c = commissions.filter(
                period__month=cm, period__year=cy,
            ).first()

            commission_value = 0
            if sc_c:
                if sc_c.period.status == CommissionPeriod.Status.ABERTA:
                    from app.apps.commissions.services import calculate_estimated_commission
                    est, _ = calculate_estimated_commission(seller, cm, cy)
                    commission_value = est
                else:
                    commission_value = sc_c.commission_amount

            comp_data.append({
                'month': f'{cm:02d}/{cy}',
                'total': mt,
                'sale_count': mc,
                'commission': commission_value,
            })

        return Response({
            'seller': {
                'uuid': str(seller.uuid),
                'name': seller.name,
                'phone': seller.phone,
                'is_active': seller.is_active,
                'commission_rate': float(seller.commission_rate),
                'created_at': (
                    seller.created_at.isoformat()
                    if seller.created_at else None
                ),
                'username': seller.user.username if seller.user else None,
            },
            'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'manual_total': manual_total,
            'link_total': link_total,
            'manual_sale_count': manual_sales_qs.count(),
            'link_sale_count': link_sales_qs.count(),
            'manual_sales': manual_sales,
            'link_sales': link_sales,
            'commissions': commissions_data,
            'evolution': evolution,
            'comparison': comp_data,
        })


class SellerReportCsvView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import csv
        import io
        import calendar

        tenant = request.user.tenant
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)

        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        hoje = timezone.localdate()

        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])

        sales = Sale.objects.filter(
            tenant=tenant, seller=seller,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        commission = SellerCommission.objects.filter(
            seller=seller,
            period__month=start.month,
            period__year=start.year,
        ).first()

        commission_amount = 0
        commission_rate_display = 0
        status_display = 'Aberta'
        if commission:
            if commission.period.status == CommissionPeriod.Status.ABERTA:
                from app.apps.commissions.services import calculate_estimated_commission
                est, _ = calculate_estimated_commission(seller, start.month, start.year)
                commission_amount = est
            else:
                commission_amount = commission.commission_amount
            commission_rate_display = float(commission.commission_rate)
            status_display = commission.period.get_status_display()

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['Data', 'Valor (R$)', 'Observacao'])
        total = 0
        for s in sales:
            writer.writerow([
                s.sale_date.isoformat(),
                f'{s.amount / 100:.2f}',
                s.notes or '',
            ])
            total += s.amount

        writer.writerow([])
        writer.writerow(['TOTAL', f'{total / 100:.2f}', ''])
        if commission:
            writer.writerow([
                'Taxa de comissao',
                f'{commission_rate_display * 100:.2f}%', '',
            ])
            writer.writerow([
                'Comissao calculada',
                f'{commission_amount / 100:.2f}', '',
            ])
            writer.writerow([
                'Status', status_display, '',
            ])
        writer.writerow([])
        writer.writerow([f'Vendedor: {seller.name}'])
        writer.writerow([f'Empresa: {tenant.company_name}'])
        writer.writerow([f'Periodo: {start.isoformat()} a {end.isoformat()}'])

        response = HttpResponse(
            buf.getvalue(), content_type='text/csv; charset=utf-8',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="{seller.name}_{start}_{end}.csv"'
        )
        return response


class SellerReportExcelView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import io
        import calendar
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill

        tenant = request.user.tenant
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)

        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        hoje = timezone.localdate()

        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])

        sales = Sale.objects.filter(
            tenant=tenant, seller=seller,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        commission = SellerCommission.objects.filter(
            seller=seller,
            period__month=start.month,
            period__year=start.year,
        ).first()

        commission_amount = 0
        commission_rate_display = 0
        status_display = 'Aberta'
        if commission:
            if commission.period.status == CommissionPeriod.Status.ABERTA:
                from app.apps.commissions.services import calculate_estimated_commission
                est, _ = calculate_estimated_commission(seller, start.month, start.year)
                commission_amount = est
            else:
                commission_amount = commission.commission_amount
            commission_rate_display = float(commission.commission_rate)
            status_display = commission.period.get_status_display()

        wb = Workbook()
        ws = wb.active
        ws.title = 'Vendas (Comissao)'

        header_font = Font(bold=True, size=12)
        total_font = Font(bold=True, size=11)
        header_fill = PatternFill(
            start_color='4361EE', end_color='4361EE', fill_type='solid',
        )
        header_font_white = Font(bold=True, color='FFFFFF', size=11)

        ws.merge_cells('A1:C1')
        ws['A1'] = f'{seller.name} — {tenant.company_name}'
        ws['A1'].font = header_font
        ws.merge_cells('A2:C2')
        ws['A2'] = (
            f'Periodo: {start.strftime("%d/%m/%Y")} '
            f'a {end.strftime("%d/%m/%Y")}'
        )
        ws['A2'].font = Font(size=10, color='666666')

        ws.append([])
        headers = ['Data', 'Valor (R$)', 'Observacao']
        ws.append(headers)
        for col in range(1, 4):
            cell = ws.cell(row=4, column=col)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        total = 0
        for s in sales:
            ws.append([
                s.sale_date.strftime('%d/%m/%Y'),
                s.amount / 100,
                s.notes or '',
            ])
            total += s.amount

        ws.append([])
        ws.append(['TOTAL', total / 100, ''])
        for col in range(1, 4):
            ws.cell(row=ws.max_row, column=col).font = total_font

        if commission:
            ws.append([])
            ws.append([
                'Taxa de comissao',
                f'{commission_rate_display * 100:.2f}%', '',
            ])
            ws.append([
                'Comissao calculada',
                commission_amount / 100, '',
            ])
            ws.append([
                'Status',
                status_display, '',
            ])

        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 18
        ws.column_dimensions['C'].width = 40

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        response = HttpResponse(
            buf.getvalue(),
            content_type=(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            ),
        )
        response['Content-Disposition'] = (
            f'attachment; filename="{seller.name}_{start}_{end}.xlsx"'
        )
        return response


class SellerReportPdfView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import calendar
        from django.template.loader import render_to_string

        tenant = request.user.tenant
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)

        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        hoje = timezone.localdate()

        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])

        sales = Sale.objects.filter(
            tenant=tenant, seller=seller,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        total = sum(s.amount for s in sales)

        commission = SellerCommission.objects.filter(
            seller=seller,
            period__month=start.month,
            period__year=start.year,
        ).first()

        commission_amount = 0
        commission_rate_display = 0
        status_display = 'Aberta'
        if commission:
            if commission.period.status == CommissionPeriod.Status.ABERTA:
                from app.apps.commissions.services import calculate_estimated_commission
                est, _ = calculate_estimated_commission(seller, start.month, start.year)
                commission_amount = est
            else:
                commission_amount = commission.commission_amount
            commission_rate_display = float(commission.commission_rate)
            status_display = commission.period.get_status_display()

        def _fmt(cents):
            r = cents // 100
            c = cents % 100
            return f'{r:,}.{c:02d}'.replace(',', '.')

        def _pct(rate):
            return f'{float(rate) * 100:.2f}%'

        total_fmt = _fmt(total)
        commission_amount_fmt = _fmt(commission_amount)
        rate_fmt = _pct(commission_rate_display)

        html = render_to_string('reports/seller_report_pdf.html', {
            'seller': seller,
            'tenant': tenant,
            'start': start,
            'end': end,
            'sales': sales,
            'total': total,
            'total_fmt': total_fmt,
            'commission': commission,
            'commission_amount': commission_amount,
            'commission_amount_fmt': commission_amount_fmt,
            'rate_fmt': rate_fmt,
            'status_display': status_display,
            'hoje': hoje,
        })

        from weasyprint import HTML
        pdf = HTML(string=html).write_pdf()

        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="{seller.name}_{start}_{end}.pdf"'
        )
        return response


class AnnualRankingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        tenant = request.user.tenant
        year = int(request.query_params.get(
            'year', timezone.localdate().year,
        ))

        rankings = SellerCommission.objects.filter(
            period__tenant=tenant,
            period__year=year,
        ).values('seller__uuid', 'seller__name').annotate(
            total=Sum('total_sold_amount'),
            commission_total=Sum('commission_amount'),
        ).order_by('-total')[:5]

        return Response({
            'year': year,
            'ranking': list(rankings),
        })


class DashboardSummaryView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        tenant = request.user.tenant
        month = request.query_params.get('month')
        year = request.query_params.get('year')

        if month:
            month = int(month)
        if year:
            year = int(year)

        from app.apps.commissions.services import get_dashboard_data
        data = get_dashboard_data(tenant, month=month, year=year)

        return Response(data)


class SellerLinkCreateView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsSellerOwner]

    def post(self, request):
        try:
            seller = request.user.seller_profile
        except Exception:
            return Response(
                {'error': 'Perfil de vendedor nao encontrado.'}, status=400,
            )

        tenant = request.user.tenant
        if not tenant:
            return Response(
                {'error': 'Usuario sem tenant.'}, status=400,
            )

        customer_name = request.data.get('customer_name', '').strip()
        amount_str = request.data.get('amount', '').strip()
        installments = int(request.data.get('installments', 1))

        if not customer_name:
            return Response(
                {'error': 'Nome do cliente e obrigatorio.'}, status=400,
            )
        if not amount_str:
            return Response(
                {'error': 'Valor e obrigatorio.'}, status=400,
            )

        try:
            amount_str = (
                amount_str.replace('R$', '').replace(',', '.').strip()
            )
            amount_cents = int(float(amount_str) * 100)
            if amount_cents <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'Valor invalido.'}, status=400)

        if installments < 1 or installments > 12:
            installments = 1

        try:
            from app.apps.orders.services import (
                create_payment_link as make_link,
            )
            order, link_url = make_link(
                tenant=tenant,
                seller=seller,
                customer_name=customer_name,
                amount_cents=amount_cents,
                installments=installments,
            )

            log_action(request, 'order.link_created', instance=order)

            return Response({
                'uuid': str(order.uuid),
                'customer_name': order.customer_name,
                'total_amount': order.total_amount,
                'link_url': link_url,
            }, status=201)

        except Exception as e:
            return Response({'error': str(e)}, status=500)


class ChangePasswordView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(request, 'user.password_changed')
        return Response({'message': 'Senha alterada com sucesso.'})
