from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from app.apps.accounts.models import User


def _check_role(request, *roles):
    if not request.user.is_authenticated:
        return False
    return request.user.role in roles


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
