from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from datetime import date, timedelta
from calendar import monthrange
from django.db.models import Sum

from app.apps.accounts.models import User
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod, SellerCommission


def mobile_login(request):
    if request.user.is_authenticated and request.user.role == User.Role.SELLER:
        return redirect('dashboard:mobile_home')
    return render(request, 'mobile/login.html')


def mobile_logout(request):
    auth_logout(request)
    return redirect('dashboard:mobile_login')


@ratelimit(key='post:identifier', rate='3/h', method='POST', block=True)
def mobile_forgot_password(request):
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        pin = request.POST.get('pin', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if pin and new_password:
            if new_password != confirm_password:
                return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'error': 'Senhas nao conferem.'})

            if len(new_password) < 8:
                return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'error': 'Senha deve ter pelo menos 8 caracteres.'})

            from app.apps.notifications.models import PasswordResetRequest as PRR
            from django.utils import timezone

            try:
                reset = PRR.objects.get(pin=pin, used=False, expires_at__gt=timezone.now())
            except PRR.DoesNotExist:
                return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'error': 'PIN invalido ou expirado.'})

            if reset.attempts >= 3:
                reset.used = True
                reset.save()
                return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'error': 'Muitas tentativas. Solicite um novo PIN.'})

            user = reset.user
            if user.username != identifier and user.email != identifier:
                reset.attempts += 1
                reset.save()
                return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'error': 'PIN nao corresponde ao usuario.'})

            user.set_password(new_password)
            user.save()
            reset.used = True
            reset.save()

            from django.contrib.auth import authenticate, login as auth_login
            user = authenticate(request, username=user.username, password=new_password)
            if user is not None:
                auth_login(request, user)
                return redirect('dashboard:mobile_home')

        if identifier:
            from app.apps.accounts.models import User as UserModel
            try:
                user = UserModel.objects.get(username=identifier)
            except UserModel.DoesNotExist:
                try:
                    user = UserModel.objects.get(email=identifier)
                except UserModel.DoesNotExist:
                    return render(request, 'mobile/forgot_password.html', {'step': 'request', 'error': 'Usuario nao encontrado.'})

            if not user.seller_profile:
                return render(request, 'mobile/forgot_password.html', {'step': 'request', 'error': 'Recuperacao de senha disponivel apenas para vendedores.'})

            from app.apps.notifications.models import PasswordResetRequest as PRR
            from django.utils.crypto import get_random_string
            from django.utils import timezone
            from datetime import timedelta

            pin = get_random_string(length=6, allowed_chars='0123456789')
            PRR.objects.create(user=user, pin=pin, expires_at=timezone.now() + timedelta(minutes=10))

            from app.apps.notifications.tasks import notify_seller_credentials
            try:
                from app.apps.notifications.models import Notification
                seller = user.seller_profile
                Notification.objects.create(
                    tenant=seller.tenant, seller=seller,
                    event_type='seller_credentials', channel='whatsapp',
                    recipient=seller.phone,
                    message_body=f'Seu PIN de recuperacao de senha QueroLink: {pin}. Valido por 10 minutos.',
                )
            except Exception:
                pass

            return render(request, 'mobile/forgot_password.html', {'step': 'verify', 'identifier': identifier, 'message': 'Um PIN de 6 digitos foi enviado via WhatsApp.'})

    return render(request, 'mobile/forgot_password.html', {'step': 'request'})


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

    from app.apps.commissions.models import SellerCommission, CommissionPeriod
    comissao_a_receber = SellerCommission.objects.filter(
        seller=seller,
        approval_status=SellerCommission.ApprovalStatus.APROVADO,
    ).exclude(
        period__status=CommissionPeriod.Status.PAGA,
    ).aggregate(total=Sum('commission_amount'))['total'] or 0

    total_vendido = Sale.objects.filter(
        seller=seller,
    ).aggregate(total=Sum('amount'))['total'] or 0

    return render(request, 'mobile/home.html', {
        'seller': seller,
        'today_total': today_total,
        'today_count': today_count,
        'month_total': month_total,
        'month_count': month_count,
        'comissao_a_receber': comissao_a_receber,
        'total_vendido': total_vendido,
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


@login_required
def mobile_links(request):
    seller = _get_seller_profile(request)
    if not seller:
        return redirect('dashboard:mobile_home')
    from app.apps.orders.models import Order
    from app.apps.payments.models import Payment
    orders = Order.objects.filter(
        seller=seller, tenant=seller.tenant
    ).select_related('seller').prefetch_related('payments').order_by('-created_at')[:50]
    orders_data = []
    for o in orders:
        try:
            link = o.payment_link
            link_url = link.gateway_url if link else None
        except Exception:
            link_url = None
        payment = o.payments.first()
        refusal = payment.refusal_reason if payment else None
        orders_data.append({
            'uuid': str(o.uuid),
            'customer_name': o.customer_name,
            'total_amount': o.total_amount,
            'status': o.status,
            'status_display': o.get_status_display(),
            'link_url': link_url,
            'refusal_reason': refusal,
            'created_at': o.created_at.isoformat(),
        })
    return render(request, 'mobile/links.html', {'seller': seller, 'orders_json': orders_data})
