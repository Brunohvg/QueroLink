from django.utils import timezone

from app.apps.orders.models import Order
from app.apps.receivables.models import Boleto


def _payment_link(order):
    try:
        return order.payment_link
    except Order.payment_link.RelatedObjectDoesNotExist:
        return None


def build_charge_center(tenant, seller=None, limit=100):
    order_filters = {'tenant': tenant}
    boleto_filters = {'tenant': tenant}
    if seller is not None:
        order_filters['seller'] = seller
        boleto_filters['seller'] = seller

    orders = Order.objects.filter(**order_filters).select_related(
        'seller', 'payment_link',
    ).prefetch_related('payments').order_by('-created_at', '-uuid')[:limit]
    boletos = Boleto.objects.filter(**boleto_filters).select_related(
        'seller', 'created_by',
    ).order_by('-created_at', '-uuid')[:limit]

    orders_data = []
    for order in orders:
        payment = next(iter(order.payments.all()), None)
        payment_link = _payment_link(order)
        orders_data.append({
            'uuid': str(order.uuid),
            'type': 'link',
            'type_display': 'Link de Pagamento' if seller is None else 'Link',
            'customer_name': order.customer_name,
            'amount_cents': order.total_amount,
            'status': order.status,
            'status_display': order.status_display_pt,
            'seller_name': order.seller.name if order.seller else '-',
            'seller_uuid': str(order.seller.uuid) if order.seller else '',
            'refusal_reason': payment.refusal_reason if payment else None,
            'link_url': (
                payment_link.gateway_url if payment_link else None
            ),
            'created_at': order.created_at.isoformat(),
            'due_date': None,
            'paid_at': (
                payment.paid_at.isoformat()
                if payment and payment.paid_at else None
            ),
            'detail_url': (
                f'/dashboard/gestor/links/{order.uuid}/'
                if seller is None else '/dashboard/mobile/links/'
            ),
        })

    boletos_data = []
    for boleto in boletos:
        seller_name = boleto.seller.name if boleto.seller else '-'
        boletos_data.append({
            'uuid': str(boleto.uuid),
            'type': 'boleto',
            'type_display': 'Boleto',
            'customer_name': boleto.payer_name or seller_name,
            'amount_cents': boleto.amount_cents,
            'paid_amount_cents': boleto.paid_amount_cents,
            'status': boleto.status,
            'status_display': boleto.get_status_display(),
            'seller_name': seller_name,
            'seller_uuid': str(boleto.seller.uuid) if boleto.seller else '',
            'refusal_reason': boleto.operation_error_message or '',
            'created_at': boleto.created_at.isoformat(),
            'due_date': boleto.due_date.isoformat(),
            'paid_at': boleto.paid_at.isoformat() if boleto.paid_at else None,
            'detail_url': (
                f'/dashboard/gestor/boletos/{boleto.uuid}/'
                if seller is None else '/dashboard/mobile/boletos/'
            ),
        })

    charges = orders_data + boletos_data
    charges.sort(key=lambda item: item['created_at'], reverse=True)
    return charges, orders_data, boletos_data


def charge_center_stats(tenant):
    today = timezone.localdate()
    orders = Order.objects.filter(tenant=tenant)
    boletos = Boleto.objects.filter(tenant=tenant)

    link_awaiting = orders.filter(status='PENDING').count()
    link_paid = orders.filter(status='COMPLETED').count()
    link_canceled = orders.filter(status__in=('CANCELED', 'EXPIRED')).count()
    boleto_awaiting = boletos.filter(
        status=Boleto.Status.PENDENTE, due_date__gte=today,
    ).count()
    boleto_overdue = boletos.filter(
        status__in=(Boleto.Status.PENDENTE, Boleto.Status.VENCIDO),
        due_date__lt=today,
    ).count()
    boleto_paid = boletos.filter(status=Boleto.Status.PAGO).count()
    boleto_canceled = boletos.filter(status__in=(
        Boleto.Status.CANCELADO,
        Boleto.Status.FALHOU,
        Boleto.Status.ESTORNADO,
        Boleto.Status.CANCEL_PEND,
        Boleto.Status.CRIANDO,
    )).count()

    return {
        'aguardando': link_awaiting + boleto_awaiting,
        'pagas': link_paid + boleto_paid,
        'vencidas': boleto_overdue,
        'canceladas': link_canceled + boleto_canceled,
        'total_links': link_awaiting + link_paid + link_canceled,
        'total_boletos': (
            boleto_awaiting + boleto_paid + boleto_overdue + boleto_canceled
        ),
        'boleto_awaiting': boleto_awaiting,
        'boleto_overdue': boleto_overdue,
        'boleto_paid': boleto_paid,
    }
