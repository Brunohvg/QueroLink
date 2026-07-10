import calendar
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)

logger = logging.getLogger(__name__)


def get_manual_sales_total(seller, month, year):
    period = get_period_by_legacy_label(seller.tenant, month, year)
    if period:
        return get_manual_sales_total_for_period(seller, period)
    start, end = legacy_month_range(month, year)
    total = Sale.objects.filter(
        tenant=seller.tenant,
        seller=seller,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=start,
        sale_date__lte=end,
    ).aggregate(t=Sum('amount'))['t'] or 0
    return total


def legacy_month_range(month, year):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    return start, date(year, month, last_day)


MESES = [
    'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]


def suggest_label(end_date):
    return f'{MESES[end_date.month - 1]}/{end_date.year}'


def suggest_period_range(tenant, month, year):
    day = getattr(tenant, 'period_start_day', 1) or 1
    if day <= 1:
        return legacy_month_range(month, year)
    if month > 1:
        start = date(year, month - 1, day)
    else:
        start = date(year - 1, 12, day)
    end = date(year, month, day - 1)
    return start, end


def get_period_by_legacy_label(tenant, month, year):
    return CommissionPeriod.objects.filter(
        tenant=tenant,
        month=month,
        year=year,
    ).exclude(status=CommissionPeriod.Status.CANCELADA).first()


def resolve_period_for_date(tenant, ref_date):
    return CommissionPeriod.objects.filter(
        tenant=tenant,
        start_date__lte=ref_date,
        end_date__gte=ref_date,
    ).exclude(status=CommissionPeriod.Status.CANCELADA).order_by('start_date').first()


def get_current_period(tenant):
    today = timezone.localdate()
    return CommissionPeriod.objects.filter(
        tenant=tenant,
        start_date__lte=today,
        end_date__gte=today,
        status__in=[
            CommissionPeriod.Status.ABERTA,
            CommissionPeriod.Status.PARCIALMENTE_FECHADA,
            CommissionPeriod.Status.PARCIALMENTE_PAGA,
        ],
    ).first()


def get_period_sales_queryset(period):
    return Sale.objects.filter(
        tenant=period.tenant,
        status='ATIVA',
        sale_date__gte=period.start_date,
        sale_date__lte=period.end_date,
    )


def get_manual_sales_total_for_period(seller, period):
    return get_period_sales_queryset(period).filter(
        seller=seller,
        origin__in=Sale.COMMISSION_ORIGINS,
    ).aggregate(t=Sum('amount'))['t'] or 0


def calculate_estimated_commission_for_period(seller, period):
    total = get_manual_sales_total_for_period(seller, period)
    rate = get_commission_rate(seller)
    commission = int((total * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return commission, total


def get_commission_rate(seller):
    rate = seller.commission_rate
    if rate is None or rate <= 0:
        rate = seller.tenant.default_commission_rate
    if rate is None or rate <= 0:
        rate = Decimal('0.01')
    return rate


def calculate_estimated_commission(seller, month, year):
    period = get_period_by_legacy_label(seller.tenant, month, year)
    if period:
        return calculate_estimated_commission_for_period(seller, period)
    total = get_manual_sales_total(seller, month, year)
    rate = get_commission_rate(seller)
    commission = int((total * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return commission, total


def get_or_create_period(tenant, month, year, expected_working_days=None):
    start, end = legacy_month_range(month, year)
    period, created = CommissionPeriod.objects.get_or_create(
        tenant=tenant,
        month=month,
        year=year,
        defaults={
            'label': suggest_label(end),
            'start_date': start,
            'end_date': end,
            'expected_working_days': expected_working_days or 22,
            'status': CommissionPeriod.Status.ABERTA,
        },
    )
    if created and expected_working_days:
        period.expected_working_days = expected_working_days
        period.save(update_fields=['expected_working_days'])
    if created:
        sync_period_seller_commissions(period)
    return period, created


def sync_period_seller_commissions(period):
    tenant = period.tenant
    sellers = Seller.objects.filter(tenant=tenant, is_active=True).select_related('tenant')
    sellers_with_manual = Seller.objects.filter(
        tenant=tenant,
        sales__origin__in=Sale.COMMISSION_ORIGINS,
        sales__sale_date__gte=period.start_date,
        sales__sale_date__lte=period.end_date,
    ).select_related('tenant')
    all_sellers = (sellers | sellers_with_manual).distinct()

    created_count = 0
    with transaction.atomic():
        for seller in all_sellers:
            sc, created = SellerCommission.objects.get_or_create(
                period=period,
                seller=seller,
                defaults={
                    'commission_rate': get_commission_rate(seller),
                    'expected_working_days': period.expected_working_days or 22,
                },
            )
            if created:
                created_count += 1
            if sc.is_editable:
                current_rate = get_commission_rate(seller)
                if sc.commission_rate != current_rate:
                    sc.commission_rate = current_rate
                sc.recalculate(commit=True)

    return created_count


def calculate_seller_working_days(period, seller):
    sales_dates = Sale.objects.filter(
        tenant=period.tenant,
        seller=seller,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=period.start_date,
        sale_date__lte=period.end_date,
    ).dates('sale_date', 'day')
    count = sales_dates.count()
    expected = period.expected_working_days or 22
    missing = max(0, expected - count)
    return count, expected, missing


def calculate_period_summary(period):
    commissions = SellerCommission.objects.filter(period=period).select_related('seller', 'period')
    total_vendido = 0
    aberta = 0
    fechada = 0
    paga = 0
    prontos = 0
    pendentes = 0
    sem_lancamento = 0
    abertos_count = 0
    fechados_count = 0
    pagos_count = 0

    for sc in commissions:
        if sc.is_editable:
            sc.recalculate(commit=False)
        total_vendido += sc.total_sold_amount
        s = sc.status
        if s == SellerCommission.Status.ABERTA or s == SellerCommission.Status.REABERTA:
            aberta += sc.commission_amount
            abertos_count += 1
        elif s in (SellerCommission.Status.FECHADA, SellerCommission.Status.AJUSTADA):
            val = sc.amount_due
            fechada += val
            fechados_count += 1
        elif s == SellerCommission.Status.PAGA:
            val = sc.amount_due
            paga += val
            pagos_count += 1

        op = sc.operational_status
        if op == SellerCommission.OperationalStatus.PRONTO:
            prontos += 1
        elif op == SellerCommission.OperationalStatus.PENDENTE:
            pendentes += 1
        else:
            sem_lancamento += 1

    return {
        'total_vendido': total_vendido,
        'commission_aberta': aberta,
        'commission_fechada': fechada,
        'commission_paga': paga,
        'vendedores_abertos': abertos_count,
        'vendedores_fechados': fechados_count,
        'vendedores_pagos': pagos_count,
        'vendedores_prontos': prontos,
        'vendedores_pendentes': pendentes,
        'vendedores_sem_lancamento': sem_lancamento,
        'total_vendedores': commissions.count(),
    }


def recalculate_period_status(period):
    summary = calculate_period_summary(period)
    total = summary['total_vendedores']
    if total == 0:
        new_status = CommissionPeriod.Status.ABERTA
    elif summary['vendedores_pagos'] == total:
        new_status = CommissionPeriod.Status.PAGA
    elif summary['vendedores_fechados'] + summary['vendedores_pagos'] == total:
        if summary['vendedores_pagos'] > 0 and summary['vendedores_fechados'] == 0:
            new_status = CommissionPeriod.Status.PAGA
        elif summary['vendedores_pagos'] > 0:
            new_status = CommissionPeriod.Status.PARCIALMENTE_PAGA
        else:
            new_status = CommissionPeriod.Status.FECHADA
    elif summary['vendedores_fechados'] > 0 or summary['vendedores_pagos'] > 0:
        if summary['vendedores_pagos'] > 0:
            new_status = CommissionPeriod.Status.PARCIALMENTE_PAGA
        else:
            new_status = CommissionPeriod.Status.PARCIALMENTE_FECHADA
    else:
        new_status = CommissionPeriod.Status.ABERTA

    period.status = new_status
    period.save(update_fields=['status', 'updated_at'])
    return new_status


def close_seller_commissions(period, seller_commission_ids, user):
    commissions = SellerCommission.objects.filter(
        id__in=seller_commission_ids,
        period=period,
        status__in=[
            SellerCommission.Status.ABERTA,
            SellerCommission.Status.REABERTA,
        ],
    ).select_related('seller', 'period')

    if not commissions.exists():
        raise ValueError('Nenhuma comissao valida para fechar.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores com comissao ABERTA ou REABERTA '
            'podem ser fechados.'
        )

    calculations = []
    with transaction.atomic():
        commissions = list(commissions.order_by('id').select_for_update())
        for sc in commissions:
            sc.freeze(user, commit=True)
            calculations.append({
                'id': sc.id,
                'seller_name': sc.seller.name,
                'total_sold': sc.total_sold_amount,
                'commission_rate': float(sc.commission_rate),
                'commission_amount': sc.commission_amount,
            })
        recalculate_period_status(period)

    try:
        tenant = period.tenant
        if tenant.accountant_email and tenant.accountant_auto_send and not period.sent_to_accounting_at:
            all_closed = not SellerCommission.objects.filter(period=period).exclude(
                status__in=[
                    SellerCommission.Status.FECHADA,
                    SellerCommission.Status.AJUSTADA,
                    SellerCommission.Status.PAGA,
                    SellerCommission.Status.CANCELADA,
                ],
            ).exists()
            if all_closed:
                from app.apps.notifications.tasks import send_accounting_package_email
                send_accounting_package_email.delay(str(tenant.uuid), period.month, period.year)
    except Exception:
        logger.error('Falha ao agendar envio contabil para period %s', period.id, exc_info=True)

    return calculations


def reopen_seller_commissions(period, seller_commission_ids, user, reason):
    if not reason or not reason.strip():
        raise ValueError('E necessario informar o motivo da reversao.')

    commissions = SellerCommission.objects.filter(
        id__in=seller_commission_ids,
        period=period,
        status__in=[
            SellerCommission.Status.FECHADA,
            SellerCommission.Status.AJUSTADA,
        ],
    )
    if not commissions.exists():
        raise ValueError('Nenhuma comissao fechada ou ajustada valida para reverter.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores com comissao FECHADA ou AJUSTADA '
            'podem ser revertidos.'
        )

    with transaction.atomic():
        commissions = list(commissions.order_by('id').select_for_update())
        for sc in commissions:
            if sc.is_paid:
                raise ValueError(
                    f'Comissao de {sc.seller.name} ja foi paga '
                    'e nao pode ser revertida.'
                )
            sc.reopen(user, reason, commit=True)
        recalculate_period_status(period)

    return list(commissions)


def pay_seller_commissions(period, seller_commission_ids, user, payment_data):
    commissions = SellerCommission.objects.filter(
        id__in=seller_commission_ids,
        period=period,
        status__in=[
            SellerCommission.Status.FECHADA,
            SellerCommission.Status.AJUSTADA,
        ],
    ).select_related('seller', 'period')
    if not commissions.exists():
        raise ValueError('Nenhuma comissao fechada ou ajustada valida para pagar.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores com comissao FECHADA ou AJUSTADA '
            'podem ser enviados para pagamento.'
        )

    payment_date = payment_data.get('payment_date')
    if payment_date:
        payment_date = date.fromisoformat(payment_date)
    else:
        payment_date = None

    with transaction.atomic():
        commissions = list(commissions.order_by('id').select_for_update())
        for sc in commissions:
            sc.mark_paid(user, {
                'payment_date': payment_date or timezone.localdate(),
                'payment_method': payment_data.get('payment_method', ''),
                'payment_notes': payment_data.get('payment_notes', ''),
            }, commit=True)
        recalculate_period_status(period)

    for sc in commissions:
        try:
            if sc.status != SellerCommission.Status.PAGA:
                continue
            from app.apps.notifications.tasks import notify_commission_paid
            notify_commission_paid(sc)
        except Exception:
            logger.error(
                'Failed to send payment notification for commission %s', sc.id,
                exc_info=True,
            )

    return list(commissions)


def create_commission_adjustment(seller_commission, new_amount, reason, user):
    with transaction.atomic():
        sc = SellerCommission.objects.select_for_update().get(pk=seller_commission.pk)
        previous_amount = sc.frozen_commission_amount or sc.commission_amount
        difference = new_amount - previous_amount

        adjustment = CommissionAdjustment.objects.create(
            seller_commission=sc,
            previous_amount=previous_amount,
            new_amount=new_amount,
            difference=difference,
            reason=reason,
            adjusted_by=user,
        )

        sc.commission_amount = new_amount
        sc.status = SellerCommission.Status.AJUSTADA
        sc.save(update_fields=['commission_amount', 'status', 'updated_at'])

    try:
        from app.apps.notifications.tasks import notify_commission_adjusted
        notify_commission_adjusted(sc, adjustment)
    except Exception:
        logger.error(
            'Failed to send adjustment notification for commission %s',
            sc.id, exc_info=True,
        )

    return adjustment


def get_links_data(tenant, month=None, year=None):
    from app.apps.orders.models import Order
    from django.db.models import Sum as _Sum

    hoje = timezone.localdate()
    if month is None:
        month = hoje.month
    if year is None:
        year = hoje.year

    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)

    orders = Order.objects.filter(
        tenant=tenant,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    total_gerados = orders.count()
    valor_gerado = orders.aggregate(t=_Sum('total_amount'))['t'] or 0

    orders_pagos = orders.filter(status='COMPLETED')
    total_pagos = orders_pagos.count()
    valor_pago = orders_pagos.aggregate(t=_Sum('total_amount'))['t'] or 0

    orders_pendentes = orders.filter(status='PENDING').count()
    orders_recusados = orders.filter(
        status__in=['CANCELED', 'EXPIRED', 'SUSPENDED'],
    ).count()

    return {
        'links_gerados': total_gerados,
        'links_pagos': total_pagos,
        'links_pendentes': orders_pendentes,
        'links_recusados': orders_recusados,
        'valor_gerado_links': valor_gerado,
        'valor_pago_links': valor_pago,
    }


def get_dashboard_data(tenant, month=None, year=None):
    hoje = timezone.localdate()

    period = None
    if month is None and year is None:
        period = get_current_period(tenant)
    else:
        if month is None:
            month = hoje.month
        if year is None:
            year = hoje.year
        period = get_period_by_legacy_label(tenant, month, year)

    if period:
        start = period.start_date
        end = period.end_date
        month = period.month
        year = period.year
    else:
        if month is None:
            month = hoje.month
        if year is None:
            year = hoje.year
        start, end = legacy_month_range(month, year)

    total_vendido = Sale.objects.filter(
        tenant=tenant,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=start,
        sale_date__lte=end,
    ).aggregate(t=Sum('amount'))['t'] or 0

    if period:
        summary = calculate_period_summary(period)
        period_status = period.status
    else:
        sellers_ativos = Seller.objects.filter(tenant=tenant, is_active=True)
        comissao_estimada = 0
        for s in sellers_ativos:
            total = Sale.objects.filter(
                tenant=tenant,
                seller=s,
                origin__in=Sale.COMMISSION_ORIGINS,
                status='ATIVA',
                sale_date__gte=start,
                sale_date__lte=end,
            ).aggregate(t=Sum('amount'))['t'] or 0
            rate = get_commission_rate(s)
            comissao_estimada += int((total * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        summary = {
            'commission_aberta': comissao_estimada,
            'commission_fechada': 0,
            'commission_paga': 0,
            'vendedores_abertos': sellers_ativos.count(),
            'vendedores_fechados': 0,
            'vendedores_pagos': 0,
            'vendedores_prontos': 0,
            'vendedores_pendentes': 0,
            'vendedores_sem_lancamento': 0,
            'total_vendedores': sellers_ativos.count(),
        }
        period_status = None

    vendedores_ativos = Seller.objects.filter(
        tenant=tenant, is_active=True,
    ).count()
    vendedores_total = Seller.objects.filter(tenant=tenant).count()

    sellers_with_sales = Seller.objects.filter(
        tenant=tenant,
        sales__origin__in=Sale.COMMISSION_ORIGINS,
        sales__sale_date__gte=start,
        sales__sale_date__lte=end,
    ).distinct().count()

    sellers_no_sale_today = Seller.objects.filter(
        tenant=tenant, is_active=True,
    ).exclude(
        sales__origin__in=Sale.COMMISSION_ORIGINS,
        sales__sale_date=hoje,
    ).count()

    links_data = get_links_data(tenant, month=month, year=year)

    top5_mes = Sale.objects.filter(
        tenant=tenant,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=start,
        sale_date__lte=end,
    ).values('seller__uuid', 'seller__name').annotate(
        total=Sum('amount'),
    ).order_by('-total')[:5]

    semana_atras = hoje - timezone.timedelta(days=7)
    sellers_inativos = list(
        Seller.objects.filter(tenant=tenant, is_active=True).exclude(
            sales__sale_date__gte=semana_atras,
            sales__origin__in=Sale.COMMISSION_ORIGINS,
            sales__status='ATIVA',
        ).values_list('name', flat=True),
    )

    sellers_with_manual_sales = Seller.objects.filter(
        tenant=tenant, is_active=True,
        sales__sale_date__gte=start,
        sales__sale_date__lte=end,
        sales__origin__in=Sale.COMMISSION_ORIGINS,
        sales__status='ATIVA',
    ).distinct().count()

    has_inconsistency = False
    if summary['total_vendedores'] > 0 and sellers_with_manual_sales > summary['total_vendedores']:
        has_inconsistency = True

    evolution = list(Sale.objects.filter(
        tenant=tenant, origin__in=Sale.COMMISSION_ORIGINS, status='ATIVA',
        sale_date__gte=start, sale_date__lte=end,
    ).values('sale_date').annotate(
        total=Sum('amount'),
    ).order_by('sale_date'))

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
    prev_total = Sale.objects.filter(
        tenant=tenant, origin__in=Sale.COMMISSION_ORIGINS, status='ATIVA',
        sale_date__gte=date(prev_year, prev_month, 1),
        sale_date__lte=date(prev_year, prev_month, prev_last_day),
    ).aggregate(t=Sum('amount'))['t'] or 0

    return {
        'period': {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'month': month,
            'year': year,
            'label': period.display_label if period else f'{month:02d}/{year}',
        },
        'total_vendido': total_vendido,
        'commission_aberta': summary['commission_aberta'],
        'commission_fechada': summary['commission_fechada'],
        'commission_paga': summary['commission_paga'],
        'period_status': period_status,
        'has_inconsistency': has_inconsistency,
        'vendedores_ativos': vendedores_ativos,
        'vendedores_total': vendedores_total,
        'vendedores_com_venda': sellers_with_sales,
        'vendedores_sem_lancamento_hoje': sellers_no_sale_today,
        'vendedores_abertos': summary['vendedores_abertos'],
        'vendedores_fechados': summary['vendedores_fechados'],
        'vendedores_pagos': summary['vendedores_pagos'],
        'vendedores_prontos': summary['vendedores_prontos'],
        'vendedores_pendentes': summary['vendedores_pendentes'],
        'vendedores_sem_lancamento_periodo': summary['vendedores_sem_lancamento'],
        'links_gerados': links_data['links_gerados'],
        'links_pagos': links_data['links_pagos'],
        'links_pendentes': links_data['links_pendentes'],
        'links_recusados': links_data['links_recusados'],
        'valor_gerado_links': links_data['valor_gerado_links'],
        'valor_pago_links': links_data['valor_pago_links'],
        'top5_mes': list(top5_mes),
        'sellers_inativos': sellers_inativos,
        'evolution': evolution,
        'comparison_prev': prev_total,
    }


def get_missing_days_before_today(seller, month, year):
    today = timezone.localdate()
    from datetime import timedelta
    period = get_period_by_legacy_label(seller.tenant, month, year)

    if period:
        start = period.start_date
        end = min(today - timedelta(days=1), period.end_date)
    else:
        start, month_end = legacy_month_range(month, year)
        end = min(today - timedelta(days=1), month_end)

    if start > end:
        return []

    submitted_dates = set(
        Sale.objects.filter(
            tenant=seller.tenant,
            seller=seller,
            origin__in=Sale.COMMISSION_ORIGINS,
            status='ATIVA',
            sale_date__gte=start,
            sale_date__lte=end,
        ).values_list('sale_date', flat=True).distinct()
    )

    missing = []
    current = start
    from app.apps.accounts.models import is_working_day
    while current <= end:
        if current not in submitted_dates:
            if is_working_day(seller.tenant, current):
                missing.append(current)
        current += timedelta(days=1)
    return missing


def ensure_seller_commission(seller, sale_date):
    period = CommissionPeriod.objects.filter(
        tenant=seller.tenant,
        start_date__lte=sale_date,
        end_date__gte=sale_date,
        status__in=[
            CommissionPeriod.Status.ABERTA,
            CommissionPeriod.Status.PARCIALMENTE_FECHADA,
            CommissionPeriod.Status.PARCIALMENTE_PAGA,
        ],
    ).first()
    if not period:
        return None
    sc, created = SellerCommission.objects.get_or_create(
        period=period, seller=seller,
        defaults={
            'commission_rate': get_commission_rate(seller),
            'expected_working_days': period.expected_working_days or 22,
        },
    )
    if sc.is_editable:
        sc.recalculate(commit=True)
    return sc


def validate_sale_can_be_changed(seller, sale_date, user):
    period = resolve_period_for_date(seller.tenant, sale_date)
    if not period:
        return True, None

    sc = SellerCommission.objects.filter(
        period=period, seller=seller,
    ).first()
    if not sc:
        return True, None

    if not sc.is_editable:
        return False, (
            f'A comissao de {seller.name} ja foi fechada '
            f'ou paga nesta competencia.'
        )
    return True, None


def has_paid_commission(period):
    return SellerCommission.objects.filter(
        period=period,
        status__in=[
            SellerCommission.Status.PAGA,
            SellerCommission.Status.AJUSTADA,
        ],
    ).exists()


def can_edit_period(period):
    if has_paid_commission(period):
        return False, 'Esta competencia possui comissao paga. Nao e possivel editar.'
    return True, None


def can_edit_period_dates(period):
    from app.apps.sales.models import Sale
    has_sales = Sale.objects.filter(
        tenant=period.tenant,
        status='ATIVA',
        sale_date__gte=period.start_date,
        sale_date__lte=period.end_date,
    ).exists()
    if has_sales:
        return False, (
            'Nao e possivel alterar datas de uma competencia '
            'que ja possui lancamentos manuais vinculados.'
        )
    has_closed = SellerCommission.objects.filter(
        period=period,
        status__in=[
            SellerCommission.Status.FECHADA,
            SellerCommission.Status.PAGA,
            SellerCommission.Status.AJUSTADA,
            SellerCommission.Status.CANCELADA,
        ],
    ).exists()
    if has_closed:
        return False, (
            'Nao e possivel alterar datas com vendedores '
            'fechados ou pagos.'
        )
    return True, None


def can_delete_period(period):
    if period.status not in (CommissionPeriod.Status.ABERTA,):
        return False, (
            f'Nao e possivel excluir competencia com status '
            f'{period.status}. Apenas competencias ABERTA podem ser excluidas.'
        )
    has_any_closed = SellerCommission.objects.filter(
        period=period,
    ).exclude(status=SellerCommission.Status.ABERTA).exists()
    if has_any_closed:
        return False, (
            'Nao e possivel excluir competencia com vendedores '
            'fechados, pagos ou ajustados.'
        )
    if has_paid_commission(period):
        return False, 'Nao e possivel excluir competencia com comissao paga.'
    return True, None


def can_cancel_period(period):
    if has_paid_commission(period):
        return False, (
            'Nao e possivel cancelar competencia com comissao paga. '
            'Crie um ajuste administrativo.'
        )
    return True, None


def update_period(period, data, user):
    can, msg = can_edit_period(period)
    if not can:
        raise ValueError(msg)

    changed_fields = []
    if 'expected_working_days' in data:
        new_days = int(data['expected_working_days'])
        if new_days != period.expected_working_days:
            period.expected_working_days = new_days
            changed_fields.append('expected_working_days')

    if 'notes' in data:
        period.notes = data['notes']
        changed_fields.append('notes')

    if 'label' in data:
        new_label = (data.get('label') or '').strip()
        if new_label != period.label:
            period.label = new_label
            changed_fields.append('label')

    month_changed = 'month' in data and int(data['month']) != period.month
    year_changed = 'year' in data and int(data['year']) != period.year
    new_start = data.get('start_date')
    new_end = data.get('end_date')
    if isinstance(new_start, str):
        try:
            new_start = date.fromisoformat(new_start)
        except ValueError:
            raise ValueError('Data inicial invalida.')
    if isinstance(new_end, str):
        try:
            new_end = date.fromisoformat(new_end)
        except ValueError:
            raise ValueError('Data final invalida.')
    start_changed = new_start is not None and new_start != period.start_date
    end_changed = new_end is not None and new_end != period.end_date

    if month_changed or year_changed or start_changed or end_changed:
        can_edit, msg = can_edit_period_dates(period)
        if not can_edit:
            raise ValueError(msg)

    if month_changed:
        period.month = int(data['month'])
        changed_fields.append('month')
    if year_changed:
        period.year = int(data['year'])
        changed_fields.append('year')
    if start_changed:
        period.start_date = new_start
        changed_fields.append('start_date')
    if end_changed:
        period.end_date = new_end
        changed_fields.append('end_date')

    if not changed_fields:
        return period, changed_fields

    try:
        period.full_clean()
    except ValidationError as exc:
        if hasattr(exc, 'message_dict'):
            messages = []
            for field_messages in exc.message_dict.values():
                messages.extend(field_messages)
            raise ValueError(' '.join(messages))
        raise ValueError(' '.join(exc.messages))
    period.save(update_fields=changed_fields + ['updated_at'])

    if 'expected_working_days' in changed_fields:
        with transaction.atomic():
            for sc in SellerCommission.objects.filter(
                period=period,
                status__in=[
                    SellerCommission.Status.ABERTA,
                    SellerCommission.Status.REABERTA,
                ],
            ):
                sc.recalculate(commit=True)

    return period, changed_fields


def delete_period(period, user):
    can, msg = can_delete_period(period)
    if not can:
        raise ValueError(msg)

    from app.apps.audit.utils import log_action

    with transaction.atomic():
        sc_count = SellerCommission.objects.filter(period=period).count()
        SellerCommission.objects.filter(period=period).delete()

        period.delete()

        log_action(
            user, 'commission_period.deleted',
            changes={
                'period_id': str(period.uuid),
                'month': period.month,
                'year': period.year,
                'sc_count': sc_count,
            }
        )

    return sc_count


def cancel_period(period, reason, user):
    if not reason or not reason.strip():
        raise ValueError('E necessario informar o motivo do cancelamento.')

    can, msg = can_cancel_period(period)
    if not can:
        raise ValueError(msg)

    with transaction.atomic():
        period.status = CommissionPeriod.Status.CANCELADA
        period.cancelled_by = user
        period.cancelled_at = timezone.now()
        period.cancel_reason = reason
        period.save(update_fields=[
            'status', 'cancelled_by', 'cancelled_at',
            'cancel_reason', 'updated_at',
        ])

        SellerCommission.objects.filter(period=period).exclude(
            status=SellerCommission.Status.PAGA,
        ).update(
            status=SellerCommission.Status.CANCELADA,
        )

    return period
