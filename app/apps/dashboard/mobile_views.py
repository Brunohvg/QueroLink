from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from datetime import date, timedelta
from calendar import monthrange
from django.db.models import Sum

from app.apps.accounts.models import User
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod, SellerCommission


def mobile_login(request):
    if request.user.is_authenticated and request.user.role == User.Role.SELLER:
        return redirect('dashboard:mobile_home')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect('dashboard:mobile_home')
        error = 'Usuario ou senha invalidos.'

    return render(request, 'mobile/login.html', {'error': error})


def mobile_logout(request):
    auth_logout(request)
    return redirect('dashboard:mobile_login')


def mobile_forgot_password(request):
    return render(request, 'mobile/forgot_password.html', {
        'message': 'Entre em contato com o administrador do sistema para redefinir sua senha.',
    })


@login_required
def mobile_home(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/home.html', {'error': 'Perfil de vendedor nao encontrado.'})

    today = timezone.localdate()
    today_sales = Sale.objects.filter(seller=seller, sale_date=today)
    today_total = sum(s.amount for s in today_sales)
    today_count = today_sales.count()

    month_total = Sale.objects.filter(
        seller=seller,
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0

    month_count = Sale.objects.filter(
        seller=seller,
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).count()

    return render(request, 'mobile/home.html', {
        'seller': seller,
        'today_total': today_total,
        'today_count': today_count,
        'month_total': month_total,
        'month_count': month_count,
    })


@login_required
def mobile_lancar_venda(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/lancar_venda.html', {'error': 'Perfil de vendedor nao encontrado.'})

    success = None
    error = None

    if request.method == 'POST':
        try:
            amount_cents = int(request.POST.get('amount_cents', '0'))
            sale_date_str = request.POST.get('sale_date', '')
            notes = request.POST.get('notes', '').strip() or None

            if amount_cents <= 0:
                raise ValueError('Valor deve ser maior que zero.')

            sale_date = date.fromisoformat(sale_date_str) if sale_date_str else timezone.localdate()

            Sale.objects.create(
                tenant=seller.tenant,
                seller=seller,
                origin=Sale.Origin.MANUAL,
                amount=amount_cents,
                sale_date=sale_date,
                notes=notes,
                created_by=request.user,
            )
            success = True
        except (ValueError, Exception) as e:
            error = str(e)

    return render(request, 'mobile/lancar_venda.html', {
        'seller': seller,
        'success': success,
        'error': error,
    })


@login_required
def mobile_minhas_vendas(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/minhas_vendas.html', {'error': 'Perfil de vendedor nao encontrado.'})

    sales = Sale.objects.filter(seller=seller).order_by('-sale_date', '-created_at')

    return render(request, 'mobile/minhas_vendas.html', {
        'seller': seller,
        'sales': sales,
    })


@login_required
def mobile_meu_desempenho(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/meu_desempenho.html', {'error': 'Perfil de vendedor nao encontrado.'})

    today = timezone.localdate()
    month_total = Sale.objects.filter(
        seller=seller,
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0

    commissions = SellerCommission.objects.filter(
        seller=seller,
    ).select_related('period').order_by('-period__year', '-period__month')

    return render(request, 'mobile/meu_desempenho.html', {
        'seller': seller,
        'month_total': month_total,
        'commissions': commissions,
        'current_month': f'{today.month:02d}/{today.year}',
    })


def _get_seller_profile(request):
    try:
        return request.user.seller_profile
    except Exception:
        return None
