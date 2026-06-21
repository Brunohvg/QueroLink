from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
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

    hoje = timezone.localdate()
    total_mes = Sale.objects.filter(
        tenant=tenant,
        sale_date__year=hoje.year,
        sale_date__month=hoje.month,
    ).aggregate(total=Sum('amount'))['total'] or 0

    competencia = CommissionPeriod.objects.filter(
        tenant=tenant,
        month=hoje.month,
        year=hoje.year,
    ).first()

    vendedores_ativos = Seller.objects.filter(tenant=tenant, is_active=True).count()

    config_ok = tenant.pagarme_configured and tenant.whatsapp_configured

    return render(request, 'dashboard/gestor/home.html', {
        'total_mes': total_mes,
        'competencia': competencia,
        'vendedores_ativos': vendedores_ativos,
        'config_ok': config_ok,
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
        whatsapp_instance_id = request.POST.get('whatsapp_instance_id', '').strip()
        whatsapp_token = request.POST.get('whatsapp_token', '').strip()
        commission_rate = request.POST.get('default_commission_rate', '').strip()

        if pagarme_api_key:
            tenant.pagarme_api_key = pagarme_api_key
        if whatsapp_instance_id:
            tenant.whatsapp_instance_id = whatsapp_instance_id
        if whatsapp_token:
            tenant.whatsapp_token = whatsapp_token
        if commission_rate:
            try:
                tenant.default_commission_rate = float(commission_rate.replace(',', '.'))
            except ValueError:
                messages.error(request, 'Taxa de comissao invalida.')
                return render(request, 'dashboard/gestor/configuracoes.html', {
                    'tenant': tenant,
                })

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
def gestor_fechamento(request):
    if not _check_role(request, User.Role.MANAGER, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/gestor/fechamento.html')


@login_required
def financeiro_fila(request):
    if not _check_role(request, User.Role.FINANCEIRO, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/fila_aprovacao.html')


@login_required
def financeiro_historico(request):
    if not _check_role(request, User.Role.FINANCEIRO, User.Role.ADMIN):
        return redirect('dashboard:home')
    return render(request, 'dashboard/financeiro/historico_pagamentos.html')
