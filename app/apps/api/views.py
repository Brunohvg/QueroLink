from datetime import date, timedelta
import logging

from rest_framework import viewsets, status, generics, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponse
from django.conf import settings
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from app.apps.sales.models import Sale, SaleChangeLog
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)
from app.apps.commissions.services import (
    calculate_estimated_commission,
    get_commission_rate,
    get_period_by_legacy_label,
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
    ManagerSaleUpdateSerializer,
    SaleChangeLogSerializer,
)
from .permissions import IsManagerOrAdmin, IsFinancialOrAdmin, IsSellerOwner
from app.apps.audit.utils import log_action

logger = logging.getLogger(__name__)


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

        from app.apps.accounts.models import mark_onboarding_step
        mark_onboarding_step(request.user.tenant, 'step_sellers')

        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        seller = self.get_object()
        if not seller.user:
            return Response(
                {'error': 'Vendedor sem usuario vinculado.'}, status=400,
            )
        from app.apps.accounts.utils import generate_temp_password
        password = generate_temp_password()
        seller.user.set_password(password)
        seller.user.save()
        try:
            from app.apps.notifications.tasks import notify_seller_credentials
            notify_seller_credentials(seller, password)
            whatsapp_sent = True
            whatsapp_error = None
        except Exception as e:
            whatsapp_sent = False
            whatsapp_error = str(e)
            logger.error(
                'WhatsApp failed on reset_password for seller %s: %s',
                seller.uuid, e, exc_info=True
            )
        log_action(request, 'seller.password_reset', instance=seller)
        return Response({
            'message': 'Senha redefinida com sucesso.',
            'whatsapp_sent': whatsapp_sent,
        })

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

    def perform_destroy(self, instance):
        if Sale.objects.filter(seller=instance).exists():
            from rest_framework import serializers as drf_ser
            raise drf_ser.ValidationError({
                'detail': 'Este vendedor possui vendas registradas e nao pode ser excluido. '
                          'Desative-o para preservar o historico.',
            })
        log_action(self.request, 'seller.deleted', instance=instance,
                   changes={'name': instance.name, 'username': instance.user.username if instance.user else None})
        user = instance.user
        instance.delete()
        if user:
            user.delete()


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

        from app.apps.commissions.services import ensure_seller_commission
        ensure_seller_commission(sale.seller, sale.sale_date)

        from app.apps.accounts.models import mark_onboarding_step
        mark_onboarding_step(self.request.user.tenant, 'step_first_sale')

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

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsManagerOrAdmin], url_path='import')
    def import_sales(self, request):
        sales_data = request.data.get('sales', [])
        if not isinstance(sales_data, list):
            return Response({'error': 'sales deve ser uma lista.'}, status=400)
        if len(sales_data) > 500:
            return Response({'error': 'Maximo 500 vendas por vez.'}, status=400)

        tenant = request.user.tenant
        imported = 0
        skipped = 0
        errors = []

        with transaction.atomic():
            for idx, item in enumerate(sales_data):
                try:
                    seller_uuid = item.get('seller_uuid')
                    amount_cents = item.get('amount_cents')
                    date_str = item.get('date')
                    notes = item.get('notes', '')

                    if not seller_uuid or not amount_cents or not date_str:
                        errors.append({'index': idx, 'error': 'Campos obrigatorios: seller_uuid, amount_cents, date'})
                        continue

                    seller = Seller.objects.filter(uuid=seller_uuid, tenant=tenant).first()
                    if not seller:
                        errors.append({'index': idx, 'error': f'Vendedor nao encontrado: {seller_uuid}'})
                        continue

                    try:
                        sale_date = date.fromisoformat(date_str)
                    except (ValueError, TypeError):
                        errors.append({'index': idx, 'error': f'Data invalida: {date_str}'})
                        continue

                    if amount_cents <= 0:
                        errors.append({'index': idx, 'error': 'Valor deve ser maior que zero.'})
                        continue

                    exists = Sale.objects.filter(
                        seller=seller, sale_date=sale_date, amount=amount_cents, tenant=tenant,
                    ).exists()
                    if exists:
                        skipped += 1
                        continue

                    sale_obj = Sale.objects.create(
                        tenant=tenant,
                        seller=seller,
                        origin=Sale.Origin.MANUAL,
                        amount=amount_cents,
                        sale_date=sale_date,
                        notes=str(notes)[:500] if notes else '',
                        created_by=request.user,
                    )
                    from app.apps.commissions.services import ensure_seller_commission
                    ensure_seller_commission(seller, sale_date)
                    imported += 1
                except Exception as e:
                    errors.append({'index': idx, 'error': str(e)})

            if errors and not imported:
                transaction.set_rollback(True)
                return Response({'error': 'Nenhuma venda importada. Corrija os erros.', 'errors': errors}, status=400)

        return Response({'imported': imported, 'skipped': skipped, 'errors': errors})

    @action(detail=True, methods=['patch'], permission_classes=[IsAuthenticated, IsManagerOrAdmin], url_path='manager-update')
    def manager_update(self, request, pk=None):
        sale = self.get_object()
        serializer = ManagerSaleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        reason = data.pop('reason')

        from app.apps.sales.services import update_sale_as_manager

        try:
            sale, changed = update_sale_as_manager(sale, request.user, data, reason)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)

        if not changed:
            return Response({'message': 'Nenhuma alteração detectada.', 'changed': False})

        return Response({
            'message': 'Venda atualizada com sucesso.',
            'changed': True,
            'sale': SaleSerializer(sale).data,
        })

    @action(detail=True, methods=['get'], permission_classes=[IsAuthenticated, IsSellerOwner | IsManagerOrAdmin], url_path='history')
    def history(self, request, pk=None):
        sale = self.get_object()
        qs = SaleChangeLog.objects.filter(
            sale=sale, tenant=request.user.tenant,
        ).select_related('changed_by').order_by('-changed_at')
        serializer = SaleChangeLogSerializer(qs, many=True)
        return Response(serializer.data)


class CommissionPeriodViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    EDITABLE_STATUSES = (
        CommissionPeriod.Status.ABERTA,
        CommissionPeriod.Status.PARCIALMENTE_FECHADA,
        CommissionPeriod.Status.PARCIALMENTE_PAGA,
    )

    def get_serializer_class(self):
        if self.action == 'create':
            return CommissionPeriodCreateSerializer
        return CommissionPeriodSerializer

    def get_queryset(self):
        qs = CommissionPeriod.objects.filter(
            tenant=self.request.user.tenant,
        )

        scope = self.request.query_params.get('scope')
        if scope == 'active':
            qs = qs.exclude(status__in=[
                CommissionPeriod.Status.PAGA,
                CommissionPeriod.Status.CANCELADA,
            ])
        elif scope == 'finished':
            qs = qs.filter(status__in=[
                CommissionPeriod.Status.PAGA,
                CommissionPeriod.Status.CANCELADA,
            ])

        month = self.request.query_params.get('month')
        if month:
            qs = qs.filter(month=int(month))
        year = self.request.query_params.get('year')
        if year:
            qs = qs.filter(year=int(year))

        return qs.prefetch_related('seller_commissions__seller')

    def list(self, request, *args, **kwargs):
        from app.apps.commissions.services import sync_period_seller_commissions
        editable = self.get_queryset().filter(status__in=self.EDITABLE_STATUSES)[:6]
        for period in editable:
            try:
                sync_period_seller_commissions(period)
            except Exception:
                logger.exception('Auto-sync falhou para period %s', period.pk)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        period = self.get_object()
        if period.status in self.EDITABLE_STATUSES:
            from app.apps.commissions.services import sync_period_seller_commissions
            sync_period_seller_commissions(period)
        return super().retrieve(request, *args, **kwargs)

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
        permission_classes=[IsAuthenticated, IsManagerOrAdmin],
    )
    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def send_accounting(self, request, pk=None):
        period = self.get_object()
        tenant = request.user.tenant

        if period.tenant_id != tenant.pk:
            return Response(
                {'error': 'Competencia nao pertence ao seu tenant.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not tenant.accountant_email:
            return Response(
                {'error': 'Cadastre o e-mail do contador em Configuracoes.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if period.status == CommissionPeriod.Status.CANCELADA:
            return Response(
                {'error': 'Competencia cancelada nao pode ser enviada.'},
                status=status.HTTP_409_CONFLICT,
            )

        force_resend = request.data.get('force_resend', False)
        if period.sent_to_accounting_at and not force_resend:
            return Response(
                {'error': 'Competencia ja foi enviada. Use force_resend para reenviar.'},
                status=status.HTTP_409_CONFLICT,
            )

        scs = list(period.seller_commissions.all())
        if not scs:
            return Response(
                {'error': 'Nenhum vendedor sincronizado nesta competencia.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        open_statuses = [
            SellerCommission.Status.ABERTA,
            SellerCommission.Status.REABERTA,
        ]
        has_open = any(sc.status in open_statuses for sc in scs)
        if has_open:
            return Response(
                {'error': 'Existem vendedores com comissao em aberto. Feche todas antes de enviar.'},
                status=status.HTTP_409_CONFLICT,
            )

        from app.apps.notifications.tasks import send_accounting_package_email

        send_accounting_package_email.delay(
            str(tenant.uuid),
            period.month,
            period.year,
            requested_by_user_id=str(request.user.pk),
        )

        log_action(
            request, 'commission_period.send_accounting', instance=period,
            changes={
                'month': period.month, 'year': period.year,
                'force_resend': force_resend,
            },
        )

        return Response({
            'message': 'Envio agendado com sucesso.',
            'detail': f'Pacote contabil sera enviado para {tenant.accountant_email}.',
        }, status=status.HTTP_202_ACCEPTED)

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

    @action(
        detail=True, methods=['get'],
        permission_classes=[IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin],
        url_path='receipt/(?P<sc_id>[^/.]+)',
    )
    def receipt(self, request, pk=None, sc_id=None):
        from django.template.loader import render_to_string
        from weasyprint import HTML

        period = self.get_object()
        try:
            sc = SellerCommission.objects.select_related(
                'seller', 'period',
            ).prefetch_related('adjustments').get(
                pk=sc_id, period=period,
            )
        except SellerCommission.DoesNotExist:
            return Response({'error': 'Comissao nao encontrada.'}, status=404)

        if sc.status != SellerCommission.Status.PAGA:
            return Response({'error': 'Recibo disponivel apenas para comissoes pagas.'}, status=404)

        adjustments = sc.adjustments.all()
        hoje = timezone.now()
        html = render_to_string('reports/recibo_comissao.html', {
            'tenant': period.tenant,
            'period': period,
            'sc': sc,
            'adjustments': adjustments,
            'hoje': hoje,
        })
        pdf = HTML(string=html).write_pdf()
        return HttpResponse(pdf, content_type='application/pdf')

    @action(
        detail=True, methods=['get'],
        permission_classes=[IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin],
        url_path='receipts',
    )
    def receipts(self, request, pk=None):
        from django.template.loader import render_to_string
        from weasyprint import HTML

        period = self.get_object()
        commissions = SellerCommission.objects.filter(
            period=period,
            status=SellerCommission.Status.PAGA,
        ).select_related('seller').prefetch_related('adjustments').order_by('seller__name')

        if not commissions.exists():
            return Response({'error': 'Nenhuma comissao paga no periodo.'}, status=404)

        hoje = timezone.now()
        pages = []
        for sc in commissions:
            adjustments = sc.adjustments.all()
            html = render_to_string('reports/recibo_comissao.html', {
                'tenant': period.tenant,
                'period': period,
                'sc': sc,
                'adjustments': adjustments,
                'hoje': hoje,
            })
            pages.append(html)

        full_html = ''.join(
            f'<div style="page-break-after: always;">{p}</div>' if i < len(pages) - 1 else p
            for i, p in enumerate(pages)
        )
        pdf = HTML(string=full_html).write_pdf()
        return HttpResponse(pdf, content_type='application/pdf')

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsManagerOrAdmin], url_path='preview')
    def preview(self, request):
        from datetime import date
        from app.apps.commissions.services import (
            calculate_estimated_commission, get_missing_days_before_today,
        )

        tenant = request.user.tenant
        hoje = timezone.localdate()
        month = int(request.query_params.get('month', hoje.month))
        year = int(request.query_params.get('year', hoje.year))

        sellers = Seller.objects.filter(tenant=tenant, is_active=True)
        sellers_data = []
        total_sold = 0
        total_commission = 0

        for seller in sellers:
            est_commission, est_total = calculate_estimated_commission(seller, month, year)
            try:
                missing_days = get_missing_days_before_today(seller, month, year)
            except Exception:
                missing_days = []
            sellers_data.append({
                'name': seller.name,
                'uuid': str(seller.uuid),
                'total_sold': est_total,
                'commission_rate': float(get_commission_rate(seller)),
                'commission_amount': est_commission,
                'has_missing_days': len(missing_days) > 0,
                'missing_days_count': len(missing_days),
            })
            total_sold += est_total
            total_commission += est_commission

        sellers_data.sort(key=lambda s: s['total_sold'], reverse=True)

        return Response({
            'sellers': sellers_data,
            'totals': {
                'total_sold': total_sold,
                'total_commission': total_commission,
            },
            'month': month,
            'year': year,
        })

@extend_schema(
    responses={200: dict},
    description='Retorna períodos com comissões fechadas/ajustadas para pagamento.',
)
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
                if sc.status in (SellerCommission.Status.FECHADA, SellerCommission.Status.AJUSTADA)
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
        except ObjectDoesNotExist:
            return Sale.objects.none()
        return Sale.objects.filter(
            seller=seller, tenant=self.request.user.tenant,
        )


@extend_schema(
    parameters=[
        OpenApiParameter('month', int, required=False),
        OpenApiParameter('year', int, required=False),
    ],
    responses={200: dict},
)
class RankingView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        from app.apps.commissions.services import (
            calculate_estimated_commission,
            calculate_estimated_commission_for_period,
            get_commission_rate,
            get_period_by_legacy_label,
            legacy_month_range,
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
        period = get_period_by_legacy_label(tenant, month, year)
        if period:
            start = period.start_date
            end = period.end_date
        else:
            start, end = legacy_month_range(month, year)

        sales = Sale.objects.filter(
            tenant=tenant,
            origin=Sale.Origin.MANUAL,
            status='ATIVA',
            sale_date__gte=start,
            sale_date__lte=end,
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
                if period:
                    commission_estimada, _ = calculate_estimated_commission_for_period(seller_obj, period)
                else:
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


@extend_schema(responses={(200, 'text/csv'): OpenApiTypes.BINARY})
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
                sc.get_status_display(),
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


@extend_schema(
    parameters=[
        OpenApiParameter('seller_id', str, location=OpenApiParameter.PATH),
        OpenApiParameter('start', str, required=False, description='Data inicial (YYYY-MM-DD)'),
        OpenApiParameter('end', str, required=False, description='Data final (YYYY-MM-DD)'),
        OpenApiParameter('month', int, required=False),
        OpenApiParameter('year', int, required=False),
    ],
    responses={200: dict},
)
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

        manual_total = manual_sales_qs.filter(status='ATIVA').aggregate(
            t=Sum('amount'),
        )['t'] or 0
        link_total = link_sales_qs.filter(status='ATIVA').aggregate(
            t=Sum('amount'),
        )['t'] or 0

        manual_sales_qs_slice = list(manual_sales_qs[:200])
        manual_sales = []
        from django.db.models import Count
        log_counts = dict(
            SaleChangeLog.objects.filter(
                sale__in=[s.pk for s in manual_sales_qs_slice],
            ).values('sale_id').annotate(
                count=Count('id'),
            ).values_list('sale_id', 'count')
        )
        for s in manual_sales_qs_slice:
            manual_sales.append({
                'uuid': str(s.uuid),
                'amount': s.amount,
                'origin': s.origin,
                'origin_display': s.get_origin_display(),
                'sale_date': s.sale_date.isoformat(),
                'notes': s.notes or '',
                'status': s.status,
                'change_log_count': log_counts.get(str(s.uuid), 0),
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

        evo = manual_sales_qs.filter(status='ATIVA').values(
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
                origin=Sale.Origin.MANUAL, status='ATIVA',
                sale_date__gte=ms, sale_date__lte=me,
            ).aggregate(t=Sum('amount'))['t'] or 0
            mc = Sale.objects.filter(
                tenant=tenant, seller=seller,
                origin=Sale.Origin.MANUAL, status='ATIVA',
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
                'phone': (seller.phone or '')[:4] + '****' + (seller.phone or '')[-4:] if len(seller.phone or '') >= 8 else '****',
                'is_active': seller.is_active,
                'commission_rate': float(get_commission_rate(seller)),
                'cpf': seller.cpf,
                'cpf_formatted': seller.cpf_formatted,
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


@extend_schema(responses={(200, 'text/csv'): OpenApiTypes.BINARY})
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


@extend_schema(responses={(200, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'): OpenApiTypes.BINARY})
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


@extend_schema(responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
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


@extend_schema(
    parameters=[OpenApiParameter('year', int, required=False)],
    responses={200: dict},
)
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


@extend_schema(
    parameters=[
        OpenApiParameter('month', int, required=False),
        OpenApiParameter('year', int, required=False),
    ],
    responses={200: dict},
)
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


@extend_schema(
    request=dict,
    responses={200: dict, 201: dict, 400: dict, 500: dict},
    methods=['GET', 'POST'],
)
class SellerLinkCreateView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsSellerOwner]

    def get(self, request):
        try:
            seller = request.user.seller_profile
        except ObjectDoesNotExist:
            return Response({'error': 'Perfil de vendedor nao encontrado.'}, status=400)

        from app.apps.orders.models import Order
        orders = Order.objects.filter(
            seller=seller, tenant=seller.tenant,
        ).select_related('seller').prefetch_related(
            'payments',
        ).order_by('-created_at')[:50]

        orders_data = []
        for o in orders:
            try:
                link = o.payment_link
                link_url = link.gateway_url if link else None
            except Exception:
                link_url = None
            payment = o.payments.first()
            refusal = payment.refusal_reason if payment else None
            orders_data.append({
                'uuid': str(o.uuid),
                'customer_name': o.customer_name,
                'total_amount': o.total_amount,
                'status': o.status,
                'status_display': o.get_status_display(),
                'link_url': link_url,
                'refusal_reason': refusal,
                'created_at': o.created_at.isoformat(),
            })

        return Response(orders_data)

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request):
        try:
            seller = request.user.seller_profile
        except ObjectDoesNotExist:
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
            return Response({'error': 'Numero de parcelas invalido (1-12).'}, status=400)

        try:
            if not tenant.pagarme_api_key:
                return Response(
                    {'error': 'Configure a chave do Pagar.me nas configuracoes.'}, status=400,
                )

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

            try:
                from app.apps.notifications.tasks import notify_seller_link_status
                notify_seller_link_status(seller, order, 'link_created')
            except Exception:
                logger.error(
                    'Failed to send link_created notification for order %s',
                    order.uuid, exc_info=True
                )

            return Response({
                'uuid': str(order.uuid),
                'customer_name': order.customer_name,
                'total_amount': order.total_amount,
                'link_url': link_url,
            }, status=201)

        except Exception as e:
            import logging
            logging.getLogger(__name__).error("SellerLinkCreateView error: %s", e, exc_info=True)
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


@extend_schema(responses={200: dict})
class WebhookStatusView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        from django.conf import settings
        from app.apps.webhooks.models import WebhookEvent

        tenant = request.user.tenant
        webhook_url = (
            f"https://{settings.SERVICE_FQDN_WEB}"
            f"/api/webhooks/pagarme/{tenant.slug}/"
        )

        last_event = (
            WebhookEvent.objects.filter(
                gateway='pagarme', tenant=tenant,
            )
            .order_by('-received_at')
            .first()
        )

        status_data = {
            'webhook_url': webhook_url,
            'tenant_slug': tenant.slug,
            'last_event': None,
        }

        if last_event:
            status_data['last_event'] = {
                'received_at': last_event.received_at.isoformat(),
                'processed': last_event.processed,
                'error': last_event.processing_error,
            }

        return Response(status_data)


class PushSubscribeView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            endpoint = request.data['endpoint']
            keys = request.data['keys']
            p256dh = keys['p256dh']
            auth = keys['auth']
        except (KeyError, TypeError):
            return Response({'error': 'Dados invalidos. Envie endpoint, keys.p256dh e keys.auth.'}, status=400)

        from app.apps.notifications.models import PushSubscription

        sub, created = PushSubscription.objects.update_or_create(
            user=request.user,
            endpoint=endpoint,
            defaults={
                'tenant': request.user.tenant,
                'p256dh': p256dh,
                'auth': auth,
                'is_active': True,
            },
        )
        return Response({'status': 'subscribed', 'created': created}, status=201 if created else 200)


class PushUnsubscribeView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        endpoint = request.data.get('endpoint', '')
        if not endpoint:
            return Response({'error': 'endpoint e obrigatorio.'}, status=400)

        from app.apps.notifications.models import PushSubscription

        PushSubscription.objects.filter(
            user=request.user, endpoint=endpoint,
        ).update(is_active=False)
        return Response(status=204)


@extend_schema(responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
class SellerStatementView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, year=None, month=None):
        if request.user.role != request.user.Role.SELLER:
            return Response({'error': 'Apenas vendedores.'}, status=403)

        try:
            seller = request.user.seller_profile
        except ObjectDoesNotExist:
            return Response({'error': 'Perfil de vendedor nao encontrado.'}, status=404)

        today = timezone.localdate()
        try:
            year_int = int(year) if year else today.year
            month_int = int(month) if month else today.month
        except (ValueError, TypeError):
            return Response({'error': 'Mes/ano invalidos.'}, status=400)

        sales = Sale.objects.filter(
            seller=seller,
            sale_date__year=year_int,
            sale_date__month=month_int,
        ).order_by('sale_date')

        sales_ativas = [s for s in sales if s.status == 'ATIVA']
        sales_estornadas = [s for s in sales if s.status == 'ESTORNADA']
        total_ativas = sum(s.amount for s in sales_ativas)
        total_estornos = sum(s.amount for s in sales_estornadas)

        from app.apps.commissions.services import calculate_estimated_commission

        commission = SellerCommission.objects.filter(
            seller=seller,
            period__month=month_int,
            period__year=year_int,
        ).select_related('period').first()

        if commission:
            comissao_valor = commission.commission_amount
            comissao_taxa = float(commission.commission_rate) * 100
            comissao_status = commission.get_status_display()
        else:
            est, _ = calculate_estimated_commission(seller, month_int, year_int)
            comissao_valor = est
            comissao_taxa = float(get_commission_rate(seller)) * 100
            comissao_status = 'Estimativa'

        from app.apps.sellers.models import SellerGoal
        goal = SellerGoal.objects.filter(
            seller=seller, month=month_int, year=year_int,
        ).first()

        semana = ['Segunda', 'Terca', 'Quarta', 'Quinta', 'Sexta', 'Sabado', 'Domingo']
        sales_data = []
        for s in sales:
            sales_data.append({
                'date': s.sale_date.strftime('%d/%m/%Y'),
                'weekday': semana[s.sale_date.weekday()],
                'amount': s.amount,
                'origin': 'Link' if s.origin == Sale.Origin.LINK else 'Manual',
                'notes': s.notes or '',
                'status': 'Estornada' if s.status == 'ESTORNADA' else 'Ativa',
                'is_estornada': s.status == 'ESTORNADA',
            })

        from django.template.loader import render_to_string
        from weasyprint import HTML

        html = render_to_string('reports/extrato_vendedor.html', {
            'logo_url': 'file://' + str(settings.BASE_DIR / 'static' / 'img' / 'vidalys-merito-logo.png'),
            'seller': seller,
            'tenant': seller.tenant,
            'competencia': f'{month_int:02d}/{year_int}',
            'data_geracao': today.strftime('%d/%m/%Y'),
            'sales_data': sales_data,
            'total_ativas': total_ativas,
            'total_estornos': total_estornos,
            'comissao_valor': comissao_valor,
            'comissao_taxa': comissao_taxa,
            'comissao_status': comissao_status,
            'goal': goal,
        })

        pdf = HTML(string=html).write_pdf()

        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="extrato_{seller.name}_{year_int}_{month_int:02d}.pdf"'
        )
        return response


@extend_schema(responses={(200, 'application/pdf'): OpenApiTypes.BINARY})
class MonthlyReportView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, year=None, month=None):
        tenant = request.user.tenant
        hoje = timezone.localdate()
        try:
            year_int = int(year) if year else hoje.year
            month_int = int(month) if month else hoje.month
        except (ValueError, TypeError):
            return Response({'error': 'Mes/ano invalidos.'}, status=400)

        from app.apps.commissions.services import calculate_estimated_commission
        period = get_period_by_legacy_label(tenant, month_int, year_int)
        sellers = Seller.objects.filter(tenant=tenant, is_active=True)
        sellers_data = []
        total_sold = 0
        total_commission_aberta = 0
        total_commission_fechada = 0
        total_commission_paga = 0

        for seller in sellers:
            est_commission, est_total = calculate_estimated_commission(seller, month_int, year_int)
            sc = SellerCommission.objects.filter(
                seller=seller,
                period__month=month_int,
                period__year=year_int,
            ).select_related('period').first()

            if sc:
                if sc.status in ('FECHADA', 'PAGA', 'AJUSTADA'):
                    st = sc
                    commission_amount = sc.commission_amount
                else:
                    commission_amount = est_commission
                    st = sc
            else:
                commission_amount = est_commission
                st = None

            status = st.get_status_display() if st else 'Estimativa'
            if st and st.status == 'FECHADA':
                total_commission_fechada += commission_amount
            elif st and st.status == 'PAGA':
                total_commission_paga += commission_amount
            else:
                total_commission_aberta += commission_amount

            sellers_data.append({
                'name': seller.name,
                'total_sold': est_total,
                'commission': commission_amount,
                'commission_rate': float(get_commission_rate(seller)) * 100,
                'status': status,
            })
            total_sold += est_total

        sellers_data.sort(key=lambda s: s['total_sold'], reverse=True)
        total_commissions = total_commission_aberta + total_commission_fechada + total_commission_paga

        prev_period = None
        if period:
            prev_period = CommissionPeriod.objects.filter(
                tenant=tenant,
                end_date__lt=period.start_date,
            ).exclude(
                status=CommissionPeriod.Status.CANCELADA,
            ).order_by('-end_date').first()

        if prev_period:
            prev_total = Sale.objects.filter(
                tenant=tenant,
                status='ATIVA',
                sale_date__gte=prev_period.start_date,
                sale_date__lte=prev_period.end_date,
            ).aggregate(t=Sum('amount'))['t'] or 0
        else:
            prev_total = 0
        variacao = round((total_sold - prev_total) / prev_total * 100) if prev_total > 0 else None

        from django.template.loader import render_to_string
        from weasyprint import HTML

        html = render_to_string('reports/relatorio_mensal.html', {
            'logo_url': 'file://' + str(settings.BASE_DIR / 'static' / 'img' / 'vidalys-merito-logo.png'),
            'tenant': tenant,
            'competencia': f'{month_int:02d}/{year_int}',
            'data_geracao': hoje.strftime('%d/%m/%Y'),
            'sellers_data': sellers_data,
            'total_sold': total_sold,
            'total_commissions': total_commissions,
            'total_aberta': total_commission_aberta,
            'total_fechada': total_commission_fechada,
            'total_paga': total_commission_paga,
            'num_sellers': sellers.count(),
            'prev_total': prev_total,
            'variacao': variacao,
        })

        pdf = HTML(string=html).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="relatorio_{year_int}_{month_int:02d}.pdf"'
        return response


class AccountingExportView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin]

    def get(self, request, year=None, month=None):
        tenant = request.user.tenant
        hoje = timezone.localdate()
        try:
            year_int = int(year) if year else hoje.year
            month_int = int(month) if month else hoje.month
        except (ValueError, TypeError):
            return Response({'error': 'Mes/ano invalidos.'}, status=400)

        features = getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {})
        if not features.get('export_contabil'):
            return Response(
                {'detail': 'Disponivel a partir do plano Pro. Faca upgrade em Configuracoes > Assinatura.',
                 'upgrade_required': True},
                status=403,
            )

        from app.apps.commissions.exports import build_accounting_zip

        zip_bytes = build_accounting_zip(tenant, month_int, year_int)

        log_action(request, 'accounting_export', changes={
            'month': month_int, 'year': year_int, 'tenant': str(tenant.uuid),
        })

        resp = HttpResponse(zip_bytes, content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="contabilidade_{year_int}_{month_int:02d}.zip"'
        return resp


class AccountingEmailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsFinancialOrAdmin | IsManagerOrAdmin]

    def post(self, request, year=None, month=None):
        tenant = request.user.tenant
        features = getattr(settings, 'PLAN_FEATURES', {}).get(tenant.plan, {})
        if not features.get('export_contabil'):
            return Response(
                {'detail': 'Disponivel a partir do plano Pro.', 'upgrade_required': True},
                status=403,
            )
        if not tenant.accountant_email:
            return Response(
                {'detail': 'Cadastre o e-mail do contador em Configuracoes.'},
                status=400,
            )
        try:
            year_int = int(year)
            month_int = int(month)
        except (ValueError, TypeError):
            return Response({'error': 'Mes/ano invalidos.'}, status=400)

        from app.apps.notifications.tasks import send_accounting_package_email
        send_accounting_package_email.delay(
            str(tenant.uuid), month_int, year_int,
            requested_by_user_id=str(request.user.pk),
        )
        return Response({'detail': f'Envio agendado para {tenant.accountant_email}.'}, status=202)
