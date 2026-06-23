import calendar
from datetime import date
from decimal import Decimal

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
            },
        )
        if created:
            created_count += 1

    if period.status == CommissionPeriod.Status.ABERTA:
        for sc in SellerCommission.objects.filter(period=period):
            sc.recalculate(commit=True)

    return created_count


def freeze_period(period, user):
    if period.status != CommissionPeriod.Status.ABERTA:
        raise ValueError(
            f'Nao e possivel fechar competencia com status {period.status}.'
        )

    sync_period_seller_commissions(period)

    commissions = period.seller_commissions.select_related('seller').all()
    calculations = []
    with transaction.atomic():
        for sc in commissions:
            sc.freeze(user, commit=True)
            calculations.append({
                'seller_name': sc.seller.name,
                'total_sold': sc.total_sold_amount,
                'commission_rate': float(sc.commission_rate),
                'commission_amount': sc.commission_amount,
            })

        period.status = CommissionPeriod.Status.FECHADA
        period.closed_by = user
        period.closed_at = timezone.now()
        period.save(update_fields=[
            'status', 'closed_by', 'closed_at', 'updated_at',
        ])

    return calculations


def mark_period_paid(period, user, payment_data):
    if period.status != CommissionPeriod.Status.FECHADA:
        raise ValueError(
            f'Nao e possivel marcar como paga competencia '
            f'com status {period.status}. O status deve ser FECHADA.'
        )

    if not period.seller_commissions.exists():
        raise ValueError(
            'Nao e possivel marcar como paga uma competencia '
            'sem vendedores/comissoes.'
        )

    payment_date = payment_data.get('payment_date')
    payment_method = payment_data.get('payment_method', '').strip()
    payment_notes = payment_data.get('payment_notes', '').strip()

    if payment_date:
        payment_date = date.fromisoformat(payment_date)
    else:
        payment_date = timezone.localdate()

    with transaction.atomic():
        for sc in period.seller_commissions.all():
            sc.paid_by = user
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
        period.paid_by = user
        period.paid_at = timezone.now()
        period.save(update_fields=[
            'status', 'paid_by', 'paid_at', 'updated_at',
        ])

    from app.apps.notifications.tasks import notify_commission_paid
    for sc in period.seller_commissions.select_related('seller').all():
        try:
            notify_commission_paid(sc)
        except Exception:
            pass

    return period


def reopen_period(period, user, reason):
    if period.status != CommissionPeriod.Status.FECHADA:
        raise ValueError(
            f'Nao e possivel reverter competencia com status {period.status}. '
            'Apenas competencias FECHADA podem ser revertidas.'
        )

    if period.paid_at or any(
        sc.paid_at for sc in period.seller_commissions.all()
    ):
        raise ValueError(
            'Nao e possivel reverter uma competencia que ja foi paga. '
            'Crie um ajuste administrativo.'
        )

    if not reason or not reason.strip():
        raise ValueError('E necessario informar o motivo da reversao.')

    with transaction.atomic():
        for sc in period.seller_commissions.all():
            sc.closed_at = None
            sc.closed_by = None
            sc.save(update_fields=['closed_at', 'closed_by'])

        period.status = CommissionPeriod.Status.ABERTA
        period.closed_at = None
        period.closed_by = None
        period.save(update_fields=[
            'status', 'closed_at', 'closed_by', 'updated_at',
        ])

    return period


def create_commission_adjustment(seller_commission, new_amount, reason, user):
    previous_amount = seller_commission.commission_amount
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
    seller_commission.save(update_fields=['commission_amount'])

    return adjustment


def get_dashboard_data(tenant, month=None, year=None):
    import calendar as _calendar
    hoje = timezone.localdate()

    if month is None:
        month = hoje.month
    if year is None:
        year = hoje.year

    start = date(year, month, 1)
    last_day = _calendar.monthrange(year, month)[1]
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

    commission_estimada = 0
    commission_fechada = 0
    commission_paga = 0
    period_status = None

    if period:
        period_status = period.status
        if period.status == CommissionPeriod.Status.ABERTA:
            for sc in SellerCommission.objects.filter(period=period):
                est, _ = calculate_estimated_commission(
                    sc.seller, month, year,
                )
                commission_estimada += est
        elif period.status == CommissionPeriod.Status.FECHADA:
            commission_fechada = SellerCommission.objects.filter(
                period=period,
            ).aggregate(t=Sum('commission_amount'))['t'] or 0
        elif period.status == CommissionPeriod.Status.PAGA:
            commission_paga = SellerCommission.objects.filter(
                period=period,
            ).aggregate(t=Sum('paid_amount'))['t'] or 0
        else:
            commission_fechada = SellerCommission.objects.filter(
                period=period,
            ).aggregate(t=Sum('commission_amount'))['t'] or 0

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

    from app.apps.orders.models import Order
    links_gerados = Order.objects.filter(tenant=tenant).count()
    links_pagos = Order.objects.filter(
        tenant=tenant, status='COMPLETED',
    ).count()

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

    return {
        'period': {
            'start': start.isoformat(),
            'end': end.isoformat(),
            'month': month,
            'year': year,
        },
        'total_vendido': total_vendido,
        'commission_estimada': commission_estimada,
        'commission_fechada': commission_fechada,
        'commission_paga': commission_paga,
        'period_status': period_status,
        'vendedores_ativos': vendedores_ativos,
        'vendedores_total': vendedores_total,
        'vendedores_com_venda': sellers_with_sales,
        'vendedores_sem_lancamento_hoje': sellers_no_sale_today,
        'links_gerados': links_gerados,
        'links_pagos': links_pagos,
        'top5_mes': list(top5_mes),
        'sellers_inativos': sellers_inativos,
    }
