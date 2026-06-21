from rest_framework import viewsets, status, generics, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
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
            tenant=self.request.user.tenant
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
        qs = Sale.objects.select_related('seller', 'order').filter(tenant=user.tenant)
        if user.role == User.Role.SELLER:
            qs = qs.filter(seller=user.seller_profile)
        return qs

    def perform_create(self, serializer):
        sale = serializer.save()
        log_action(self.request, 'sale.created', instance=sale)

    def perform_update(self, serializer):
        sale = serializer.save()
        log_action(self.request, 'sale.updated', instance=sale)

    def perform_destroy(self, instance):
        from app.apps.commissions.models import CommissionPeriod
        if CommissionPeriod.is_locked_for(instance.tenant, instance.sale_date):
            raise drf_serializers.ValidationError({
                'detail': 'Este periodo ja foi fechado. Nao e possivel excluir vendas deste mes.'
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

        log_action(request, 'commission_period.closed', instance=period,
                   changes={'month': period.month, 'year': period.year})
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

        log_action(request, 'commission_period.sent', instance=period,
                   changes={'month': period.month, 'year': period.year})
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
        seller_commissions = request.data.get('seller_commissions', None)
        if seller_commissions:
            for sc in period.seller_commissions.all():
                if str(sc.id) in seller_commissions:
                    sc.approval_status = SellerCommission.ApprovalStatus.APROVADO
                    sc.save(update_fields=['approval_status'])
        else:
            for sc in period.seller_commissions.all():
                sc.approval_status = SellerCommission.ApprovalStatus.APROVADO
                sc.save(update_fields=['approval_status'])

        all_approved = all(
            sc.approval_status == SellerCommission.ApprovalStatus.APROVADO
            for sc in period.seller_commissions.all()
        )
        if all_approved:
            period.status = CommissionPeriod.Status.APROVADA
            period.approved_at = timezone.now()
            period.save(update_fields=['status', 'approved_at', 'updated_at'])
            log_action(request, 'commission_period.approved', instance=period,
                       changes={'month': period.month, 'year': period.year})
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
        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response(
                {'error': 'E necessario informar o motivo da rejeicao para devolver ao gestor.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        period.status = CommissionPeriod.Status.EM_CONFERENCIA
        period.sent_to_financial_at = None
        for sc in period.seller_commissions.all():
            sc.approval_status = SellerCommission.ApprovalStatus.PENDENTE
            sc.save(update_fields=['approval_status'])
        period.save(update_fields=['status', 'sent_to_financial_at', 'updated_at'])
        log_action(request, 'commission_period.rejected', instance=period,
                   changes={'month': period.month, 'year': period.year, 'reason': reason})
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

        log_action(request, 'commission_period.paid', instance=period,
                   changes={'month': period.month, 'year': period.year})

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


class SellerDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        from datetime import date, timedelta

        tenant = request.user.tenant
        try:
            seller = Seller.objects.select_related('user').get(uuid=seller_id, tenant=tenant)
        except Seller.DoesNotExist:
            return Response({'error': 'Vendedor nao encontrado.'}, status=404)

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
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            end = date(year, month, last_day)

        sales_qs = Sale.objects.filter(
            tenant=tenant, seller=seller,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('-sale_date', '-created_at')

        total_amount = sales_qs.aggregate(t=Sum('amount'))['t'] or 0

        sales = []
        for s in sales_qs[:200]:
            sales.append({
                'uuid': str(s.uuid),
                'amount': s.amount,
                'origin': s.origin,
                'origin_display': s.get_origin_display(),
                'sale_date': s.sale_date.isoformat(),
                'notes': s.notes or '',
            })

        commissions = SellerCommission.objects.filter(
            seller=seller,
        ).select_related('period').order_by('-period__year', '-period__month')

        commissions_data = []
        for sc in commissions:
            commissions_data.append({
                'period_month': sc.period.month,
                'period_year': sc.period.year,
                'period_status': sc.period.status,
                'total_sold_amount': sc.total_sold_amount,
                'commission_rate': float(sc.commission_rate),
                'commission_amount': sc.commission_amount,
            })

        evo = sales_qs.values('sale_date').annotate(day_total=Sum('amount')).order_by('sale_date')
        evolution = [{'date': e['sale_date'].isoformat(), 'total': e['day_total']} for e in evo]

        comp_data = []
        for m in range(5, -1, -1):
            cm = hoje.month - m
            cy = hoje.year
            if cm <= 0:
                cm += 12
                cy -= 1
            ms = date(cy, cm, 1)
            import calendar
            me = date(cy, cm, calendar.monthrange(cy, cm)[1])
            mt = Sale.objects.filter(
                tenant=tenant, seller=seller,
                sale_date__gte=ms, sale_date__lte=me,
            ).aggregate(t=Sum('amount'))['t'] or 0
            mc = Sale.objects.filter(
                tenant=tenant, seller=seller,
                sale_date__gte=ms, sale_date__lte=me,
            ).count()
            sc_c = commissions.filter(period__month=cm, period__year=cy).first()
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
                'created_at': seller.created_at.isoformat() if seller.created_at else None,
                'username': seller.user.username if seller.user else None,
            },
            'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'total_amount': total_amount,
            'sale_count': sales_qs.count(),
            'sales': sales,
            'commissions': commissions_data,
            'evolution': evolution,
            'comparison': comp_data,
        })


class SellerReportCsvView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import csv, io

        tenant = request.user.tenant
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)

        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')
        hoje = timezone.localdate()

        if start_str and end_str:
            from datetime import date
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        else:
            from datetime import date
            import calendar
            month = int(request.query_params.get('month', hoje.month))
            year = int(request.query_params.get('year', hoje.year))
            start = date(year, month, 1)
            end = date(year, month, calendar.monthrange(year, month)[1])

        sales = Sale.objects.filter(
            tenant=tenant, seller=seller,
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['Data', 'Valor (R$)', 'Origem', 'Observacao'])
        total = 0
        for s in sales:
            writer.writerow([
                s.sale_date.isoformat(),
                f'{s.amount / 100:.2f}',
                s.get_origin_display(),
                s.notes or '',
            ])
            total += s.amount

        writer.writerow([])
        writer.writerow(['TOTAL', f'{total / 100:.2f}', '', ''])
        writer.writerow([])
        writer.writerow([f'Vendedor: {seller.name}'])
        writer.writerow([f'Empresa: {tenant.company_name}'])
        writer.writerow([f'Periodo: {start.isoformat()} a {end.isoformat()}'])

        response = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{seller.name}_{start}_{end}.csv"'
        return response


class SellerReportExcelView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        import io
        from datetime import date
        import calendar
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, numbers

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
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        wb = Workbook()
        ws = wb.active
        ws.title = 'Vendas'

        header_font = Font(bold=True, size=12)
        total_font = Font(bold=True, size=11)
        header_fill = PatternFill(start_color='4361EE', end_color='4361EE', fill_type='solid')
        header_font_white = Font(bold=True, color='FFFFFF', size=11)

        ws.merge_cells('A1:D1')
        ws['A1'] = f'{seller.name} — {tenant.company_name}'
        ws['A1'].font = header_font
        ws.merge_cells('A2:D2')
        ws['A2'] = f'Periodo: {start.strftime("%d/%m/%Y")} a {end.strftime("%d/%m/%Y")}'
        ws['A2'].font = Font(size=10, color='666666')

        ws.append([])
        headers = ['Data', 'Valor (R$)', 'Origem', 'Observacao']
        ws.append(headers)
        for col in range(1, 5):
            cell = ws.cell(row=4, column=col)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        total = 0
        for s in sales:
            ws.append([
                s.sale_date.strftime('%d/%m/%Y'),
                s.amount / 100,
                s.get_origin_display(),
                s.notes or '',
            ])
            total += s.amount

        ws.append([])
        ws.append(['TOTAL', total / 100, '', ''])
        for col in range(1, 5):
            ws.cell(row=ws.max_row, column=col).font = total_font

        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 16
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 40

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        response = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{seller.name}_{start}_{end}.xlsx"'
        return response


class SellerReportPdfView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, seller_id=None):
        from datetime import date
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
            sale_date__gte=start, sale_date__lte=end,
        ).order_by('sale_date')

        total = sum(s.amount for s in sales)

        html = render_to_string('reports/seller_report_pdf.html', {
            'seller': seller,
            'tenant': tenant,
            'start': start,
            'end': end,
            'sales': sales,
            'total': total,
            'hoje': hoje,
        })

        from weasyprint import HTML
        pdf = HTML(string=html).write_pdf()

        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{seller.name}_{start}_{end}.pdf"'
        return response


class AnnualRankingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        tenant = request.user.tenant
        year = int(request.query_params.get('year', timezone.localdate().year))

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
        from datetime import date, timedelta
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
            sale_date__gte=start, sale_date__lte=end,
        ).aggregate(t=Sum('amount'))['t'] or 0

        comissao_a_pagar = SellerCommission.objects.filter(
            period__tenant=tenant,
            approval_status=SellerCommission.ApprovalStatus.APROVADO,
        ).exclude(period__status=CommissionPeriod.Status.PAGA).aggregate(
            t=Sum('commission_amount')
        )['t'] or 0

        top5_mes = Sale.objects.filter(
            tenant=tenant,
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
        last_day_lastyear = calendar.monthrange(start.year - 1, start.month)[1]
        end_lastyear = date(start.year - 1, start.month, last_day_lastyear)
        total_ano_anterior = Sale.objects.filter(
            tenant=tenant,
            sale_date__gte=same_month_last_year,
            sale_date__lte=end_lastyear,
        ).aggregate(t=Sum('amount'))['t'] or 0

        semana_atras = hoje - timedelta(days=7)
        sellers_inativos = list(
            Seller.objects.filter(tenant=tenant, is_active=True).exclude(
                sales__sale_date__gte=semana_atras
            ).values_list('name', flat=True)
        )

        vendedores_ativos = Seller.objects.filter(tenant=tenant, is_active=True).count()
        vendedores_total = Seller.objects.filter(tenant=tenant).count()

        return Response({
            'period': {'start': start.isoformat(), 'end': end.isoformat()},
            'total_vendido': total_vendido,
            'comissao_a_pagar': comissao_a_pagar,
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
            return Response({'error': 'Perfil de vendedor nao encontrado.'}, status=400)

        tenant = request.user.tenant
        if not tenant:
            return Response({'error': 'Usuario sem tenant.'}, status=400)

        customer_name = request.data.get('customer_name', '').strip()
        amount_str = request.data.get('amount', '').strip()
        installments = int(request.data.get('installments', 1))

        if not customer_name:
            return Response({'error': 'Nome do cliente e obrigatorio.'}, status=400)
        if not amount_str:
            return Response({'error': 'Valor e obrigatorio.'}, status=400)

        try:
            amount_str = amount_str.replace('R$', '').replace(',', '.').strip()
            amount_cents = int(float(amount_str) * 100)
            if amount_cents <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'Valor invalido.'}, status=400)

        if installments < 1 or installments > 12:
            installments = 1

        try:
            from app.apps.orders.services import create_payment_link as make_link
            order, link_url = make_link(
                tenant=tenant,
                seller=seller,
                customer_name=customer_name,
                amount_cents=amount_cents,
                installments=installments,
            )

            from app.apps.audit.utils import log_action
            log_action(request, 'order.link_created', instance=order)

            return Response({
                'uuid': str(order.uuid),
                'customer_name': order.customer_name,
                'total_amount': order.total_amount,
                'link_url': link_url,
            }, status=201)

        except Exception as e:
            return Response({'error': str(e)}, status=500)
