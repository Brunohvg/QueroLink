import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from app.apps.accounts.models import User, tenant_has_feature

from .models import Boleto


logger = logging.getLogger(__name__)


def _check_role(request, *roles):
    if not request.user.is_authenticated:
        return False
    return request.user.role in roles


@login_required
def gestor_boletos(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return redirect('dashboard:gestor_home')

    tenant = request.user.tenant
    boletos_qs = Boleto.objects.filter(tenant=tenant).select_related(
        'seller', 'created_by',
    ).order_by('-created_at')[:200]

    boletos_data = []
    for b in boletos_qs:
        boletos_data.append({
            'uuid': str(b.uuid),
            'seller_name': b.seller.name,
            'amount_cents': b.amount_cents,
            'status': b.status,
            'status_display': b.get_status_display(),
            'due_date': b.due_date.isoformat(),
            'paid_at': b.paid_at.isoformat() if b.paid_at else None,
            'paid_amount_cents': b.paid_amount_cents,
            'created_at': b.created_at.isoformat(),
        })

    stats_qs = Boleto.objects.filter(tenant=tenant)
    today = timezone.localdate()
    stats = {
        'total_a_receber': stats_qs.filter(
            status=Boleto.Status.PENDENTE,
        ).count(),
        'vencidos': stats_qs.filter(
            status=Boleto.Status.PENDENTE, due_date__lt=today,
        ).count(),
        'pagos_hoje': stats_qs.filter(
            status=Boleto.Status.PAGO, paid_at__date=today,
        ).count(),
    }

    return render(request, 'dashboard/gestor/boletos/list.html', {
        'boletos_json': boletos_data,
        'stats': stats,
    })


@login_required
def gestor_boleto_detalhe(request, boleto_uuid):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return redirect('dashboard:gestor_home')

    from django.shortcuts import get_object_or_404
    boleto = get_object_or_404(
        Boleto, uuid=boleto_uuid, tenant=request.user.tenant,
    )

    data = {
        'uuid': str(boleto.uuid),
        'seller_name': boleto.seller.name,
        'created_by_name': boleto.created_by.get_full_name(),
        'amount_cents': boleto.amount_cents,
        'status': boleto.status,
        'status_display': boleto.get_status_display(),
        'due_date': boleto.due_date.isoformat(),
        'paid_at': boleto.paid_at.isoformat() if boleto.paid_at else None,
        'paid_amount_cents': boleto.paid_amount_cents,
        'instructions': boleto.instructions,
        'notes': boleto.notes,
        'provider_order_id': boleto.provider_order_id,
        'provider_charge_id': boleto.provider_charge_id,
        'last_provider_status': boleto.last_provider_status,
        'operation_error_code': boleto.operation_error_code,
        'operation_error_message': boleto.operation_error_message,
        'has_invoice_pdf': bool(boleto.invoice_pdf.name),
        'has_invoice_xml': bool(boleto.invoice_xml.name),
        'boleto_uuid': str(boleto.uuid),
    }

    return render(request, 'dashboard/gestor/boletos/detail.html', {
        'boleto_json': data,
        'boleto': boleto,
    })


@login_required
def gestor_boleto_new(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return redirect('dashboard:gestor_home')

    from app.apps.sellers.models import Seller
    sellers = Seller.objects.filter(
        tenant=request.user.tenant, is_active=True,
    ).values('uuid', 'name').order_by('name')

    return render(request, 'dashboard/gestor/boletos/new.html', {
        'sellers_json': list(sellers),
    })


from django.utils import timezone
