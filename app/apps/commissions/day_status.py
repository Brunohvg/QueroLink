"""PROMPT 48 - estado operacional dos dias de uma competencia.

Helper central que classifica cada dia (settled) de uma CommissionPeriod para
um vendedor em exatamente um estado operacional:

- LANCADO      -> existe venda manual ativa no dia (2 vendas = 1 dia lancado);
- JUSTIFICADO  -> sem venda manual ativa e existe SellerDayJustification;
- PENDENTE     -> dia esperado (util) passado, sem venda e sem justificativa;
- NAO_UTIL     -> domingo/feriado/dia nao util (regra is_working_day do tenant).

A justificativa NUNCA tem valor financeiro: aqui ela so muda o estado do dia,
nunca soma total/comissao/ranking. Datas sao civis (DateField), sem timezone.
"""

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from app.apps.accounts.models import is_working_day
from app.apps.commissions.models import SellerCommission
from app.apps.commissions.services import is_period_editable_for_seller
from app.apps.sales.models import Sale
from app.apps.sellers.models import SellerDayJustification

LANCADO = 'LANCADO'
JUSTIFICADO = 'JUSTIFICADO'
PENDENTE = 'PENDENTE'
NAO_UTIL = 'NAO_UTIL'


def _settled_range(period, reference_date=None):
    """Dias 'liquidados' da competencia: do inicio ate ontem (ou fim do periodo).

    O dia de hoje ainda nao e cobravel (mesma regra de
    get_missing_days_for_period), logo nao entra como PENDENTE.
    """
    today = reference_date or timezone.localdate()
    start = period.start_date
    end = min(today - timedelta(days=1), period.end_date)
    return start, end


def _empty_summary(period_editable):
    return _summary_from_counts(
        {LANCADO: 0, JUSTIFICADO: 0, PENDENTE: 0, NAO_UTIL: 0},
        period_editable,
    )


def _summary_from_counts(counts, period_editable):
    launched = counts[LANCADO]
    justified = counts[JUSTIFICADO]
    pending = counts[PENDENTE]
    non_working = counts[NAO_UTIL]
    resolved = launched + justified
    expected = launched + justified + pending
    operational_status = 'PRONTO' if pending == 0 else 'PENDENTE'
    return {
        'launched_days_count': launched,
        'justified_days_count': justified,
        'pending_days_count': pending,
        'non_working_days_count': non_working,
        'expected_working_days': expected,
        'resolved_days_count': resolved,
        'operational_status': operational_status,
        # Aliases legados mantidos para compatibilidade das telas/APIs atuais.
        'lancados': launched,
        'justificados': justified,
        'pendentes': pending,
        'nao_util': non_working,
        'pending_days': pending,
        'period_editable': period_editable,
    }


def _classify(expected, sale, just):
    if not expected:
        return NAO_UTIL
    if sale:
        return LANCADO
    if just:
        return JUSTIFICADO
    return PENDENTE


def _build_day(d, expected, sale, just, period_editable):
    status = _classify(expected, sale, just)
    can_manage = period_editable and status in (PENDENTE, JUSTIFICADO)
    return {
        'date': d,
        'is_expected_day': expected,
        'status': status,
        'active_sales_count': sale[0] if sale else 0,
        'active_sales_total': sale[1] if sale else 0,
        'justification_uuid': str(just.uuid) if just else None,
        'justification_reason': just.reason if just else None,
        'justification_reason_display': just.get_reason_display() if just else None,
        'justification_notes': just.notes if just else '',
        'can_manage_justification': can_manage,
    }


def get_period_day_statuses(tenant, seller, period, reference_date=None, sc=None):
    """Estados operacionais dos dias de um vendedor numa competencia.

    Queries: 1 (vendas agregadas por dia) + 1 (justificativas) + ate 1 (SC),
    independente do numero de dias. Sem N+1 por dia.
    """
    period_editable = is_period_editable_for_seller(seller, period, sc=sc)
    start, end = _settled_range(period, reference_date)

    if start > end:
        return {'days': [], 'summary': _empty_summary(period_editable)}

    sales_by_day = {}
    for row in Sale.objects.filter(
        tenant=tenant, seller=seller,
        origin__in=Sale.COMMISSION_ORIGINS, status='ATIVA',
        sale_date__gte=start, sale_date__lte=end,
    ).values('sale_date').annotate(c=Count('uuid'), t=Sum('amount')):
        sales_by_day[row['sale_date']] = (row['c'], row['t'] or 0)

    just_by_day = {
        j.date: j
        for j in SellerDayJustification.objects.filter(
            tenant=tenant, seller=seller,
            date__gte=start, date__lte=end,
        )
    }

    days = []
    counts = {LANCADO: 0, JUSTIFICADO: 0, PENDENTE: 0, NAO_UTIL: 0}
    d = start
    while d <= end:
        expected = is_working_day(tenant, d)
        sale = sales_by_day.get(d)
        just = just_by_day.get(d)
        day = _build_day(d, expected, sale, just, period_editable)
        counts[day['status']] += 1
        days.append(day)
        d += timedelta(days=1)

    return {
        'days': days,
        'summary': _summary_from_counts(counts, period_editable),
    }


def get_period_summary_bulk(tenant, period, sellers, reference_date=None):
    """Resumo (lancados/justificados/pendentes/nao_util) por vendedor, em LOTE.

    Queries constantes (independentes do numero de vendedores/dias):
    1 (vendas do range agrupadas por seller+dia) + 1 (justificativas) +
    1 (SellerCommission do periodo). Usado no fechamento do gestor.
    Retorna dict seller_id -> summary.
    """
    start, end = _settled_range(period, reference_date)
    seller_ids = [s.pk for s in sellers]

    sc_by_seller = {
        sc.seller_id: sc
        for sc in SellerCommission.objects.filter(
            period=period, seller_id__in=seller_ids,
        )
    }

    # Editabilidade computada em memoria (sem query por vendedor).
    from app.apps.commissions.services import (
        _LOCKED_PERIOD_STATUSES, _LOCKED_SC_STATUSES,
    )
    period_locked = period.status in _LOCKED_PERIOD_STATUSES

    def _editable(sc):
        if period_locked:
            return False
        if sc and sc.status in _LOCKED_SC_STATUSES:
            return False
        return True

    result = {}
    if start > end:
        for s in sellers:
            result[s.pk] = _empty_summary(_editable(sc_by_seller.get(s.pk)))
        return result

    sale_days = {sid: set() for sid in seller_ids}
    for row in Sale.objects.filter(
        tenant=tenant, seller_id__in=seller_ids,
        origin__in=Sale.COMMISSION_ORIGINS, status='ATIVA',
        sale_date__gte=start, sale_date__lte=end,
    ).values('seller_id', 'sale_date').distinct():
        sale_days.setdefault(row['seller_id'], set()).add(row['sale_date'])

    just_days = {sid: set() for sid in seller_ids}
    for row in SellerDayJustification.objects.filter(
        tenant=tenant, seller_id__in=seller_ids,
        date__gte=start, date__lte=end,
    ).values('seller_id', 'date'):
        just_days.setdefault(row['seller_id'], set()).add(row['date'])

    # Dias uteis do range (computados uma unica vez).
    working_days = []
    d = start
    while d <= end:
        working_days.append((d, is_working_day(tenant, d)))
        d += timedelta(days=1)

    for s in sellers:
        s_sales = sale_days.get(s.pk, set())
        s_justs = just_days.get(s.pk, set())
        counts = {LANCADO: 0, JUSTIFICADO: 0, PENDENTE: 0, NAO_UTIL: 0}
        for d, expected in working_days:
            if not expected:
                counts[NAO_UTIL] += 1
            elif d in s_sales:
                counts[LANCADO] += 1
            elif d in s_justs:
                counts[JUSTIFICADO] += 1
            else:
                counts[PENDENTE] += 1
        result[s.pk] = _summary_from_counts(
            counts, _editable(sc_by_seller.get(s.pk)),
        )
    return result
