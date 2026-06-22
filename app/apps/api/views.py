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
    SaleSerializer,
    SaleCreateSerializer,
    CommissionPeriodSerializer,
    CommissionPeriodCreateSerializer,
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
        return Response({'password': password, 'username': seller.user.username})


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
        if CommissionPeriod.is_locked_for(instance.tenant, instance.sale_date):
            raise drf_serializers.ValidationError({
                'detail': (
                    'Este periodo ja foi fechado. '
                    'Nao e possivel excluir vendas deste mes.'
                ),
            })
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

        for seller in Seller.objects.filter(tenant=tenant, is_active=True):
            SellerCommission.objects.get_or_create(
                period=period,
                seller=seller,
                defaults={'commission_rate': seller.commission_rate},
            )

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        period = self.get_object()
        if period.status != CommissionPeriod.Status.ABERTA:
            return Response(
                {'error': (
                    f'Nao e possivel fechar competencia '
                    f'com status {period.status}.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for sc in period.seller_commissions.select_related('seller').all():
                sc.freeze(request.user, commit=True)

            period.status = CommissionPeriod.Status.FECHADA
            period.closed_by = request.user
            period.closed_at = timezone.now()
            period.save(update_fields=[
                'status', 'closed_by', 'closed_at', 'updated_at',
            ])

        log_action(
            request, 'commission_period.closed', instance=period,
            changes={'month': period.month, 'year': period.year},
        )
        serializer = self.get_serializer(period)
        return Response(serializer.data)

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin | IsFinancialOrAdmin],
    )
    def mark_paid(self, request, pk=None):
        period = self.get_object()
        if period.status != CommissionPeriod.Status.FECHADA:
            return Response(
                {'error': (
                    f'Nao e possivel marcar como paga competencia '
                    f'com status {period.status}. O status deve ser FECHADA.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_date = request.data.get('payment_date')
        payment_method = request.data.get('payment_method', '').strip()
        payment_notes = request.data.get('payment_notes', '').strip()

        if payment_date:
            try:
                payment_date = date.fromisoformat(payment_date)
            except (ValueError, TypeError):
                return Response(
                    {'error': 'Data de pagamento invalida.'}, status=400,
                )
        else:
            payment_date = timezone.localdate()

        with transaction.atomic():
            for sc in period.seller_commissions.all():
                sc.paid_by = request.user
                sc.paid_at = timezone.now()
                sc.payment_date = payment_date
                sc.paid_amount = sc.commission_amount
                sc.payment_method = payment_method or None
                sc.payment_notes = payment_notes or None
                sc.save(update_fields=[
                    'paid_by', 'paid_at', 'payment_date',
                    'paid_amount', 'payment_method', 'payment_notes',
                ])

            period.status = CommissionPeriod.Status.PAGA
            period.paid_by = request.user
            period.paid_at = timezone.now()
            period.save(update_fields=[
                'status', 'paid_by', 'paid_at', 'updated_at',
            ])

        log_action(
            request, 'commission_period.paid', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'payment_date': str(payment_date),
            },
        )

        from app.apps.notifications.tasks import notify_commission_paid
        for sc in period.seller_commissions.select_related('seller').all():
            try:
                notify_commission_paid(sc)
            except Exception:
                pass

        return Response(self.get_serializer(period).data)

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    def adjust(self, request, pk=None):
        period = self.get_object()
        if period.status not in (
            CommissionPeriod.Status.FECHADA,
            CommissionPeriod.Status.PAGA,
            CommissionPeriod.Status.AJUSTADA,
        ):
            return Response(
                {'error': (
                    f'Nao e possivel ajustar competencia '
                    f'com status {period.status}.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response(
                {'error': 'E necessario informar o motivo do ajuste.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        adjustments_data = request.data.get('adjustments', [])
        if not adjustments_data:
            return Response(
                {'error': 'Informe ao menos um ajuste de comissao.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            for adj in adjustments_data:
                sc_id = adj.get('seller_commission_id')
                new_amount = adj.get('new_amount')

                if not sc_id or new_amount is None:
                    continue

                try:
                    sc = period.seller_commissions.get(id=sc_id)
                except SellerCommission.DoesNotExist:
                    continue

                previous_amount = sc.commission_amount
                difference = new_amount - previous_amount

                CommissionAdjustment.objects.create(
                    seller_commission=sc,
                    previous_amount=previous_amount,
                    new_amount=new_amount,
                    difference=difference,
                    reason=reason,
                    adjusted_by=request.user,
                )

                sc.commission_amount = new_amount
                sc.save(update_fields=['commission_amount'])

            period.status = CommissionPeriod.Status.AJUSTADA
            period.adjusted_by = request.user
            period.adjusted_at = timezone.now()
            period.adjustment_reason = reason
            period.save(update_fields=[
                'status', 'adjusted_by', 'adjusted_at',
                'adjustment_reason', 'updated_at',
            ])

        log_action(
            request, 'commission_period.adjusted', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'reason': reason,
            },
        )
        return Response(self.get_serializer(period).data)

    @action(
        detail=True, methods=['post'],
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    def cancel(self, request, pk=None):
        period = self.get_object()
        if period.status not in (
            CommissionPeriod.Status.FECHADA,
            CommissionPeriod.Status.AJUSTADA,
        ):
            return Response(
                {'error': (
                    f'Nao e possivel cancelar competencia '
                    f'com status {period.status}.'
                )},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response(
                {'error': 'E necessario informar o motivo do cancelamento.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        period.status = CommissionPeriod.Status.CANCELADA
        period.cancelled_by = request.user
        period.cancelled_at = timezone.now()
        period.cancel_reason = reason
        period.save(update_fields=[
            'status', 'cancelled_by', 'cancelled_at',
            'cancel_reason', 'updated_at',
        ])

        log_action(
            request, 'commission_period.cancelled', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'reason': reason,
            },
        )
        return Response(self.get_serializer(period).data)


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
        tenant = request.user.tenant
        month = int(request.query_params.get(
            'month', timezone.localdate().month,
        ))
        year = int(request.query_params.get(
            'year', timezone.localdate().year,
        ))

        sales = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
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
            commissions_data.append({
                'period_month': sc.period.month,
                'period_year': sc.period.year,
                'period_status': sc.period.status,
                'total_sold_amount': sc.total_sold_amount,
                'commission_rate': float(sc.commission_rate),
                'commission_amount': sc.commission_amount,
                'closed_at': sc.closed_at.isoformat() if sc.closed_at else None,
                'paid_at': sc.paid_at.isoformat() if sc.paid_at else None,
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
            comp_data.append({
                'month': f'{cm:02d}/{cy}',
                'total': mt,
                'sale_count': mc,
                'commission': sc_c.commission_amount if sc_c else 0,
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
                f'{float(commission.commission_rate) * 100:.2f}%', '',
            ])
            writer.writerow([
                'Comissao calculada',
                f'{commission.commission_amount / 100:.2f}', '',
            ])
            writer.writerow([
                'Status', commission.period.get_status_display(), '',
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
                f'{float(commission.commission_rate) * 100:.2f}%', '',
            ])
            ws.append([
                'Comissao calculada',
                commission.commission_amount / 100, '',
            ])
            ws.append([
                'Status',
                commission.period.get_status_display(), '',
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

        html = render_to_string('reports/seller_report_pdf.html', {
            'seller': seller,
            'tenant': tenant,
            'start': start,
            'end': end,
            'sales': sales,
            'total': total,
            'commission': commission,
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
        import calendar

        tenant = request.user.tenant
        hoje = timezone.localdate()
        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')

        if start_str and end_str:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])

        total_vendido = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).aggregate(t=Sum('amount'))['t'] or 0

        comissao_a_pagar = SellerCommission.objects.filter(
            period__tenant=tenant,
            period__status__in=[
                CommissionPeriod.Status.ABERTA,
                CommissionPeriod.Status.FECHADA,
            ],
        ).aggregate(t=Sum('commission_amount'))['t'] or 0

        comissao_fechada = SellerCommission.objects.filter(
            period__tenant=tenant,
            period__status=CommissionPeriod.Status.FECHADA,
        ).aggregate(t=Sum('commission_amount'))['t'] or 0

        comissao_paga = SellerCommission.objects.filter(
            period__tenant=tenant,
            period__status=CommissionPeriod.Status.PAGA,
        ).aggregate(t=Sum('paid_amount'))['t'] or 0

        top5_mes = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=start, sale_date__lte=end,
        ).values('seller__name').annotate(
            total=Sum('amount'),
        ).order_by('-total')[:5]

        top5_ano = SellerCommission.objects.filter(
            period__tenant=tenant,
            period__year=hoje.year,
        ).values('seller__name').annotate(
            total=Sum('total_sold_amount'),
        ).order_by('-total')[:5]

        same_month_last_year = start.replace(year=start.year - 1)
        last_day_lastyear = calendar.monthrange(
            start.year - 1, start.month,
        )[1]
        end_lastyear = date(start.year - 1, start.month, last_day_lastyear)
        total_ano_anterior = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            sale_date__gte=same_month_last_year,
            sale_date__lte=end_lastyear,
        ).aggregate(t=Sum('amount'))['t'] or 0

        semana_atras = hoje - timedelta(days=7)
        sellers_inativos = list(
            Seller.objects.filter(tenant=tenant, is_active=True).exclude(
                sales__sale_date__gte=semana_atras,
                sales__origin=Sale.Origin.MANUAL,
            ).values_list('name', flat=True),
        )

        vendedores_ativos = Seller.objects.filter(
            tenant=tenant, is_active=True,
        ).count()
        vendedores_total = Seller.objects.filter(tenant=tenant).count()

        return Response({
            'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'total_vendido': total_vendido,
            'comissao_a_pagar': comissao_a_pagar,
            'comissao_fechada': comissao_fechada,
            'comissao_paga': comissao_paga,
            'top5_mes': list(top5_mes),
            'top5_ano': list(top5_ano),
            'total_ano_anterior': total_ano_anterior,
            'sellers_inativos': sellers_inativos,
            'vendedores_ativos': vendedores_ativos,
            'vendedores_total': vendedores_total,
        })


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
