import json

from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import (
    authenticate, login as auth_login, logout as auth_logout,
)
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
from app.apps.audit.utils import log_action


def mobile_login(request):
    if (
        request.user.is_authenticated
        and request.user.role == User.Role.SELLER
    ):
        return redirect('dashboard:mobile_home')
    return render(request, 'mobile/login.html')


def mobile_logout(request):
    auth_logout(request)
    return redirect('dashboard:mobile_login')


@ratelimit(key='post:identifier', rate='3/h', method='POST', block=True)
@csrf_exempt
def mobile_forgot_password(request):
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        pin = request.POST.get('pin', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get(
            'confirm_password', '',
        ).strip()

        if pin and new_password:
            if new_password != confirm_password:
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'verify', 'identifier': identifier,
                    'error': 'Senhas nao conferem.',
                })

            if len(new_password) < 8:
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'verify', 'identifier': identifier,
                    'error': 'Senha deve ter pelo menos 8 caracteres.',
                })

            from app.apps.notifications.models import (
                PasswordResetRequest as PRR,
            )
            from django.utils import timezone as tz

            try:
                resets = PRR.objects.filter(
                    used=False, expires_at__gt=tz.now(),
                ).order_by('-expires_at')
                reset = None
                for r in resets:
                    if r.check_pin(pin):
                        reset = r
                        break
                if not reset:
                    raise PRR.DoesNotExist
            except PRR.DoesNotExist:
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'verify', 'identifier': identifier,
                    'error': 'PIN invalido ou expirado.',
                })

            if reset.attempts >= 3:
                reset.used = True
                reset.save()
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'verify', 'identifier': identifier,
                    'error': (
                        'Muitas tentativas. Solicite um novo PIN.'
                    ),
                })

            user = reset.user
            if user.username != identifier and user.email != identifier:
                reset.attempts += 1
                reset.save()
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'verify', 'identifier': identifier,
                    'error': 'PIN nao corresponde ao usuario.',
                })

            user.set_password(new_password)
            user.save()
            reset.used = True
            reset.save()

            user = authenticate(
                request, username=user.username, password=new_password,
            )
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
                    return render(request, 'mobile/forgot_password.html', {
                        'step': 'request',
                        'error': 'Usuario nao encontrado.',
                    })

            if not user.seller_profile:
                return render(request, 'mobile/forgot_password.html', {
                    'step': 'request',
                    'error': (
                        'Recuperacao de senha disponivel '
                        'apenas para vendedores.'
                    ),
                })

            from app.apps.notifications.models import (
                PasswordResetRequest as PRR,
            )
            from django.utils.crypto import get_random_string
            from django.utils import timezone as tz
            from datetime import timedelta as td

            pin = get_random_string(length=6, allowed_chars='0123456789')
            reset = PRR(
                user=user,
                expires_at=tz.now() + td(minutes=10),
            )
            reset.set_pin(pin)
            reset.save()

            try:
                from app.apps.notifications.models import Notification
                seller = user.seller_profile
                Notification.objects.create(
                    tenant=seller.tenant, seller=seller,
                    event_type='seller_credentials', channel='whatsapp',
                    recipient=seller.phone,
                    message_body=(
                        f'Seu PIN de recuperacao de senha QueroLink: '
                        f'{pin}. Valido por 10 minutos.'
                    ),
                )
            except Exception:
                pass

            return render(request, 'mobile/forgot_password.html', {
                'step': 'verify', 'identifier': identifier,
                'message': (
                    'Um PIN de 6 digitos foi enviado via WhatsApp.'
                ),
            })

    return render(
        request, 'mobile/forgot_password.html', {'step': 'request'},
    )


@login_required
def mobile_home(request):
    try:
        seller = _get_seller_profile(request)
        if not seller:
            return render(request, 'mobile/home.html', {
                'error': 'Perfil de vendedor nao encontrado.',
            })

        today = timezone.localdate()
        today_manual = Sale.objects.filter(
            seller=seller, origin=Sale.Origin.MANUAL, sale_date=today,
        ).first()
        today_total = today_manual.amount if today_manual else 0
        has_entry_today = today_manual is not None

        month_total = Sale.objects.filter(
            seller=seller,
            origin=Sale.Origin.MANUAL,
            sale_date__year=today.year,
            sale_date__month=today.month,
        ).aggregate(total=Sum('amount'))['total'] or 0

        month_link_total = Sale.objects.filter(
            seller=seller,
            origin=Sale.Origin.LINK,
            sale_date__year=today.year,
            sale_date__month=today.month,
        ).aggregate(total=Sum('amount'))['total'] or 0

        from app.apps.commissions.services import (
            calculate_estimated_commission, sync_period_seller_commissions,
        )

        period = CommissionPeriod.objects.filter(
            tenant=seller.tenant,
            month=today.month,
            year=today.year,
        ).first()

        if period:
            sync_period_seller_commissions(period)

        sc = SellerCommission.objects.filter(
            seller=seller,
            period__month=today.month,
            period__year=today.year,
        ).first()

        sc_status = sc.status if sc else None
        is_editable = sc.is_editable if sc else True

        if sc_status in (SellerCommission.Status.ABERTA, SellerCommission.Status.REABERTA):
            comissao_valor, month_total = calculate_estimated_commission(
                seller, today.month, today.year,
            )
            comissao_label = 'Estimada'
        elif sc_status == SellerCommission.Status.FECHADA:
            comissao_valor = sc.frozen_commission_amount or sc.commission_amount
            comissao_label = 'Fechada'
        elif sc_status == SellerCommission.Status.PAGA:
            comissao_valor = sc.paid_amount or sc.frozen_commission_amount or sc.commission_amount
            comissao_label = 'Paga'
        elif sc_status == SellerCommission.Status.AJUSTADA:
            comissao_valor = sc.commission_amount
            comissao_label = 'Ajustada'
        else:
            if not sc:
                comissao_valor, _ = calculate_estimated_commission(
                    seller, today.month, today.year,
                )
                comissao_label = 'Estimada'
            else:
                comissao_valor = sc.commission_amount
                comissao_label = ''

        periodo_status = period.status if period else None

        return render(request, 'mobile/home.html', {
            'seller': seller,
            'today_total': today_total,
            'has_entry_today': has_entry_today,
            'month_total': month_total,
            'month_link_total': month_link_total,
            'comissao_estimada': comissao_valor,
            'comissao_label': comissao_label,
            'periodo_status': periodo_status,
            'is_editable': is_editable,
            'seller_commission_status': sc_status,
        })
    except Exception as e:
        return render(request, 'mobile/home.html', {
            'error': f'Erro ao carregar pagina: {str(e)}',
        })


@login_required
def mobile_lancar_venda(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/lancar_venda.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    success = None
    error = None
    existing_sale = None
    last_amount = 0
    was_update = False

    if request.method == 'POST':
        try:
            amount_cents = int(request.POST.get('amount_cents', '0'))
            sale_date_str = request.POST.get('sale_date', '')
            notes = request.POST.get('notes', '').strip() or None

            if amount_cents <= 0:
                raise ValueError('Valor deve ser maior que zero.')

            sale_date = (
                date.fromisoformat(sale_date_str)
                if sale_date_str else timezone.localdate()
            )

            today = timezone.localdate()
            if sale_date > today:
                raise ValueError(
                    'Nao e possivel lancar vendas em data futura.',
                )

            if (
                sale_date.year < today.year
                or (
                    sale_date.year == today.year
                    and sale_date.month < today.month
                )
            ):
                raise ValueError(
                    'Nao e possivel lancar ou editar vendas de meses '
                    'anteriores. Entre em contato com seu gestor.',
                )

            from app.apps.commissions.services import validate_sale_can_be_changed
            can_change, error_msg = validate_sale_can_be_changed(seller, sale_date, request.user)
            if not can_change:
                raise ValueError(error_msg)

            existing = Sale.objects.filter(
                seller=seller,
                origin=Sale.Origin.MANUAL,
                sale_date=sale_date,
            ).first()

            if existing:
                existing.amount = amount_cents
                existing.notes = notes
                existing.updated_by = request.user
                existing.save()
                log_action(
                    request, 'sale.updated', instance=existing,
                    changes={
                        'sale_date': str(sale_date),
                        'amount': amount_cents,
                    },
                )
            else:
                sale = Sale.objects.create(
                    tenant=seller.tenant,
                    seller=seller,
                    origin=Sale.Origin.MANUAL,
                    amount=amount_cents,
                    sale_date=sale_date,
                    notes=notes,
                    created_by=request.user,
                )
                log_action(
                    request, 'sale.created', instance=sale,
                )

            success = True
            last_amount = amount_cents
            was_update = existing is not None
        except (ValueError, Exception) as e:
            error = str(e)
            last_amount = 0
            was_update = False

    if request.method == 'GET':
        sale_date_str = request.GET.get('date', '')
        if sale_date_str:
            try:
                query_date = date.fromisoformat(sale_date_str)
                existing_sale = Sale.objects.filter(
                    seller=seller,
                    origin=Sale.Origin.MANUAL,
                    sale_date=query_date,
                ).first()
            except (ValueError, Exception):
                pass

    return render(request, 'mobile/lancar_venda.html', {
        'seller': seller,
        'success': success,
        'error': error,
        'existing_sale': existing_sale,
        'last_amount': last_amount if success else 0,
        'was_update': was_update if success else False,
    })


@login_required
def mobile_minhas_vendas(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/minhas_vendas.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    sales = Sale.objects.filter(seller=seller).order_by(
        '-sale_date', '-created_at',
    )

    return render(request, 'mobile/minhas_vendas.html', {
        'seller': seller,
        'sales': sales,
    })


@login_required
def mobile_meu_desempenho(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/meu_desempenho.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    today = timezone.localdate()
    month_total = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.MANUAL,
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0

    from app.apps.commissions.services import calculate_estimated_commission

    commissions = SellerCommission.objects.filter(
        seller=seller,
    ).select_related('period').order_by('-period__year', '-period__month')

    commissions_data = []
    for sc in commissions:
        if sc.period.status == CommissionPeriod.Status.ABERTA:
            est, total_est = calculate_estimated_commission(
                seller, sc.period.month, sc.period.year,
            )
            sc.total_sold_amount = total_est
            sc.commission_amount = est
        commissions_data.append(sc)

    return render(request, 'mobile/meu_desempenho.html', {
        'seller': seller,
        'month_total': month_total,
        'commissions': commissions_data,
        'current_month': f'{today.month:02d}/{today.year}',
    })


def _get_seller_profile(request):
    try:
        return request.user.seller_profile
    except Exception:
        return None


@login_required
def mobile_perfil(request):
    seller = _get_seller_profile(request)
    if not seller:
        return render(request, 'mobile/perfil.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    return render(request, 'mobile/perfil.html', {
        'seller': seller,
    })


@login_required
def mobile_links(request):
    seller = _get_seller_profile(request)
    if not seller:
        return redirect('dashboard:mobile_home')
    from app.apps.orders.models import Order
    from app.apps.payments.models import Payment
    orders = Order.objects.filter(
        seller=seller, tenant=seller.tenant,
    ).select_related('seller').prefetch_related(
        'payments',
    ).order_by('-created_at')[:50]
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
    return render(request, 'mobile/links.html', {
        'seller': seller, 'orders_json': json.dumps(orders_data),
    })
