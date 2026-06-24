import calendar
from datetime import date
from decimal import Decimal

from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Q

from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import (
    CommissionPeriod,
    SellerCommission,
    CommissionAdjustment,
)


def get_manual_sales_total(seller, month, year):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    total = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.MANUAL,
        sale_date__gte=start,
        sale_date__lte=end,
    ).aggregate(t=Sum('amount'))['t'] or 0
    return total


def get_manual_sales_by_day(seller, month, year):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    sales = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.MANUAL,
        sale_date__gte=start,
        sale_date__lte=end,
    ).order_by('sale_date')
    return sales


def get_commission_rate(seller):
    rate = seller.commission_rate
    if rate is None or rate <= 0:
        rate = seller.tenant.default_commission_rate
    if rate is None or rate <= 0:
        rate = Decimal('0.01')
    return rate


def calculate_estimated_commission(seller, month, year):
    total = get_manual_sales_total(seller, month, year)
    rate = get_commission_rate(seller)
    commission = int(float(total) * float(rate) + 0.5)
    return commission, total


def get_or_create_period(tenant, month, year, expected_working_days=None):
    period, created = CommissionPeriod.objects.get_or_create(
        tenant=tenant,
        month=month,
        year=year,
        defaults={
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
    sellers = Seller.objects.filter(tenant=tenant, is_active=True)
    sellers_with_manual = Seller.objects.filter(
        tenant=tenant,
        sales__origin=Sale.Origin.MANUAL,
        sales__sale_date__year=period.year,
        sales__sale_date__month=period.month,
    )
    all_sellers = (sellers | sellers_with_manual).distinct()

    created_count = 0
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
            sc.recalculate(commit=True)

    return created_count


def get_seller_commission(period, seller):
    sc, _ = SellerCommission.objects.get_or_create(
        period=period,
        seller=seller,
        defaults={
            'commission_rate': get_commission_rate(seller),
            'expected_working_days': period.expected_working_days or 22,
        },
    )
    if sc.is_editable:
        sc.recalculate(commit=True)
    return sc


def calculate_seller_working_days(period, seller):
    sales_dates = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.MANUAL,
        sale_date__year=period.year,
        sale_date__month=period.month,
    ).dates('sale_date', 'day')
    count = sales_dates.count()
    expected = period.expected_working_days or 22
    missing = max(0, expected - count)
    return count, expected, missing


def calculate_period_summary(period):
    commissions = SellerCommission.objects.filter(period=period)
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
        elif s == SellerCommission.Status.FECHADA:
            val = sc.frozen_commission_amount or sc.commission_amount
            fechada += val
            fechados_count += 1
        elif s == SellerCommission.Status.PAGA:
            val = sc.paid_amount or sc.frozen_commission_amount or sc.commission_amount
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
    )
    if not commissions.exists():
        raise ValueError('Nenhuma comissao valida para fechar.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores com comissao ABERTA ou REABERTA '
            'podem ser fechados.'
        )

    calculations = []
    with transaction.atomic():
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

    return calculations


def reopen_seller_commissions(period, seller_commission_ids, user, reason):
    if not reason or not reason.strip():
        raise ValueError('E necessario informar o motivo da reversao.')

    commissions = SellerCommission.objects.filter(
        id__in=seller_commission_ids,
        period=period,
        status=SellerCommission.Status.FECHADA,
    )
    if not commissions.exists():
        raise ValueError('Nenhuma comissao fechada valida para reverter.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores FECHADOS e ainda nao pagos '
            'podem ser revertidos.'
        )

    with transaction.atomic():
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
        status=SellerCommission.Status.FECHADA,
    )
    if not commissions.exists():
        raise ValueError('Nenhuma comissao fechada valida para pagar.')
    if commissions.count() != len(seller_commission_ids):
        raise ValueError(
            'Apenas vendedores com comissao FECHADA '
            'podem ser enviados para pagamento.'
        )

    payment_date = payment_data.get('payment_date')
    if payment_date:
        payment_date = date.fromisoformat(payment_date)
    else:
        payment_date = None

    with transaction.atomic():
        for sc in commissions:
            sc.mark_paid(user, {
                'payment_date': payment_date or timezone.localdate(),
                'payment_method': payment_data.get('payment_method', ''),
                'payment_notes': payment_data.get('payment_notes', ''),
            }, commit=True)
        recalculate_period_status(period)

    for sc in commissions:
        try:
            from app.apps.notifications.tasks import notify_commission_paid
            notify_commission_paid(sc)
        except Exception:
            pass

    return list(commissions)


def create_commission_adjustment(seller_commission, new_amount, reason, user):
    previous_amount = seller_commission.frozen_commission_amount or seller_commission.commission_amount
    difference = new_amount - previous_amount

    adjustment = CommissionAdjustment.objects.create(
        seller_commission=seller_commission,
        previous_amount=previous_amount,
        new_amount=new_amount,
        difference=difference,
        reason=reason,
        adjusted_by=user,
    )

    seller_commission.commission_amount = new_amount
    seller_commission.status = SellerCommission.Status.AJUSTADA
    seller_commission.save(update_fields=['commission_amount', 'status'])

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

    if month is None:
        month = hoje.month
    if year is None:
        year = hoje.year

    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)

    total_vendido = Sale.objects.filter(
        tenant=tenant,
        origin=Sale.Origin.MANUAL,
        sale_date__gte=start,
        sale_date__lte=end,
    ).aggregate(t=Sum('amount'))['t'] or 0

    period = CommissionPeriod.objects.filter(
        tenant=tenant, month=month, year=year,
    ).first()

    if period:
        sync_period_seller_commissions(period)

    if period:
        summary = calculate_period_summary(period)
        period_status = period.status
    else:
        summary = {
            'commission_aberta': 0,
            'commission_fechada': 0,
            'commission_paga': 0,
            'vendedores_abertos': 0,
            'vendedores_fechados': 0,
            'vendedores_pagos': 0,
            'vendedores_prontos': 0,
            'vendedores_pendentes': 0,
            'vendedores_sem_lancamento': 0,
        }
        period_status = None

    vendedores_ativos = Seller.objects.filter(
        tenant=tenant, is_active=True,
    ).count()
    vendedores_total = Seller.objects.filter(tenant=tenant).count()

    sellers_with_sales = Seller.objects.filter(
        tenant=tenant,
        sales__origin=Sale.Origin.MANUAL,
        sales__sale_date__gte=start,
        sales__sale_date__lte=end,
    ).distinct().count()

    sellers_no_sale_today = Seller.objects.filter(
        tenant=tenant, is_active=True,
    ).exclude(
        sales__origin=Sale.Origin.MANUAL,
        sales__sale_date=hoje,
    ).count()

    links_data = get_links_data(tenant, month=month, year=year)

    top5_mes = Sale.objects.filter(
        tenant=tenant,
        origin=Sale.Origin.MANUAL,
        sale_date__gte=start,
        sale_date__lte=end,
    ).values('seller__uuid', 'seller__name').annotate(
        total=Sum('amount'),
    ).order_by('-total')[:5]

    semana_atras = hoje - timezone.timedelta(days=7)
    sellers_inativos = list(
        Seller.objects.filter(tenant=tenant, is_active=True).exclude(
            sales__sale_date__gte=semana_atras,
            sales__origin=Sale.Origin.MANUAL,
        ).values_list('name', flat=True),
    )

    sellers_with_manual_sales = Seller.objects.filter(
        tenant=tenant, is_active=True,
        sales__sale_date__gte=start,
        sales__sale_date__lte=end,
        sales__origin=Sale.Origin.MANUAL,
    ).distinct().count()

    has_inconsistency = False
    if summary['total_vendedores'] > 0 and sellers_with_manual_sales > summary['total_vendedores']:
        has_inconsistency = True

    return {
        'period': {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'month': month,
            'year': year,
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
    }


def validate_sale_can_be_changed(seller, sale_date, user):
    period = CommissionPeriod.objects.filter(
        tenant=seller.tenant,
        month=sale_date.month,
        year=sale_date.year,
    ).first()
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
        sale_date__year=period.year,
        sale_date__month=period.month,
    ).exists()
    if has_sales:
        return False, (
            'Nao e possivel alterar mes ou ano de uma competencia '
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
            'Nao e possivel alterar mes ou ano com vendedores '
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

    month_changed = 'month' in data and int(data['month']) != period.month
    year_changed = 'year' in data and int(data['year']) != period.year

    if month_changed or year_changed:
        can_edit, msg = can_edit_period_dates(period)
        if not can_edit:
            raise ValueError(msg)

    if month_changed:
        period.month = int(data['month'])
        changed_fields.append('month')
    if year_changed:
        period.year = int(data['year'])
        changed_fields.append('year')

    if not changed_fields:
        return period, changed_fields

    period.save(update_fields=changed_fields + ['updated_at'])

    if 'expected_working_days' in changed_fields:
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

    sc_count = SellerCommission.objects.filter(period=period).count()
    SellerCommission.objects.filter(period=period).delete()

    period.delete()

    return sc_count


def cancel_period(period, reason, user):
    if not reason or not reason.strip():
        raise ValueError('E necessario informar o motivo do cancelamento.')

    can, msg = can_cancel_period(period)
    if not can:
        raise ValueError(msg)

    period.status = CommissionPeriod.Status.CANCELADA
    period.cancelled_by = user
    period.cancelled_at = __import__('django').utils.timezone.now()
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