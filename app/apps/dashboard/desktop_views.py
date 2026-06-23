from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
from app.apps.accounts.models import User, Tenant
from app.apps.sales.models import Sale
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod


def _check_role(request, *roles):
    if not request.user.is_authenticated:
        return False
    return request.user.role in roles


@login_required
def gestor_home(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.commissions.services import get_dashboard_data
    data = get_dashboard_data(tenant)

    hoje = timezone.localdate()
    config_ok = tenant.pagarme_configured and tenant.whatsapp_configured

    def _fmt(val):
        r = val // 100
        c = val % 100
        return f'{r:,}.{c:02d}'.replace(',', '.')

    competencia = CommissionPeriod.objects.filter(
        tenant=tenant,
        month=hoje.month,
        year=hoje.year,
    ).first()

    public_url = f"https://{settings.SERVICE_FQDN_WEB}/loja/{tenant.slug}/"

    return render(request, 'dashboard/gestor/home.html', {
        'total_mes': data['total_vendido'],
        'total_mes_fmt': _fmt(data['total_vendido']),
        'comissao_estimada': data['commission_estimada'] + data['commission_fechada'] + data['commission_paga'],
        'comissao_estimada_fmt': _fmt(data['commission_estimada'] + data['commission_fechada'] + data['commission_paga']),
        'competencia': competencia,
        'vendedores_ativos': data['vendedores_ativos'],
        'config_ok': config_ok,
        'total_orders': data['links_gerados'],
        'paid_orders_count': data['links_pagos'],
        'public_url': public_url,
        'dashboard_data': data,
    })


@login_required
def gestor_configuracoes(request):
    if not _check_role(request, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    if request.method == 'POST':
        pagarme_api_key = request.POST.get('pagarme_api_key', '').strip()
        whatsapp_instance_id = request.POST.get(
            'whatsapp_instance_id', '',
        ).strip()
        whatsapp_token = request.POST.get('whatsapp_token', '').strip()
        commission_rate = request.POST.get(
            'default_commission_rate', '',
        ).strip()

        if pagarme_api_key:
            tenant.pagarme_api_key = pagarme_api_key
        if whatsapp_instance_id:
            tenant.whatsapp_instance_id = whatsapp_instance_id
        if whatsapp_token:
            tenant.whatsapp_token = whatsapp_token
        if commission_rate:
            try:
                tenant.default_commission_rate = float(
                    commission_rate.replace(',', '.'),
                )
            except ValueError:
                messages.error(request, 'Taxa de comissao invalida.')
                return render(
                    request, 'dashboard/gestor/configuracoes.html',
                    {'tenant': tenant},
                )

        tenant.save()
        messages.success(request, 'Configuracoes salvas com sucesso.')
        return redirect('dashboard:gestor_configuracoes')

    return render(request, 'dashboard/gestor/configuracoes.html', {
        'tenant': tenant,
    })


@login_required
def gestor_ranking(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/ranking.html')


@login_required
def gestor_vendedores(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/vendedores.html')


@login_required
def gestor_vendedor_detalhe(request, seller_id):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    try:
        seller = Seller.objects.get(uuid=seller_id, tenant=tenant)
    except Seller.DoesNotExist:
        return redirect('dashboard:gestor_vendedores')

    return render(request, 'dashboard/gestor/vendedor_detalhe.html', {
        'seller': seller,
        'seller_json': {
            'uuid': str(seller.uuid),
            'name': seller.name,
        },
    })


@login_required
def gestor_fechamento(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/fechamento.html')


@login_required
def financeiro_fila(request):
    if not _check_role(
        request, User.Role.FINANCEIRO, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/fila_aprovacao.html')


@login_required
def financeiro_historico(request):
    if not _check_role(
        request, User.Role.FINANCEIRO, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/historico_pagamentos.html')


@login_required
def gestor_links(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')

    tenant = request.user.tenant
    if not tenant:
        return redirect('dashboard:home')

    from app.apps.orders.models import Order

    seller_uuid = request.GET.get('seller')
    orders = Order.objects.filter(
        tenant=tenant,
    ).select_related('seller').order_by('-created_at')[:100]
    if seller_uuid:
        orders = orders.filter(seller__uuid=seller_uuid)

    orders_data = []
    for o in orders:
        payment = o.payments.first()
        refusal = payment.refusal_reason if payment else None
        orders_data.append({
            'uuid': str(o.uuid),
            'customer_name': o.customer_name,
            'amount': o.total_amount,
            'status': o.status,
            'status_display': o.get_status_display(),
            'seller_name': o.seller.name if o.seller else '-',
            'refusal_reason': refusal,
            'created_at': o.created_at.isoformat(),
        })

    from app.apps.sellers.models import Seller as SellerModel
    sellers = list(SellerModel.objects.filter(
        tenant=tenant, is_active=True,
    ).values('uuid', 'name'))

    return render(request, 'dashboard/gestor/links.html', {
        'orders_json': orders_data,
        'sellers': sellers,
    })
