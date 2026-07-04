import json
import logging

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

logger = logging.getLogger(__name__)


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

            from app.apps.accounts.models import User as UserModel
            from app.apps.notifications.models import (
                PasswordResetRequest as PRR,
            )
            from django.utils import timezone as tz

            try:
                user = UserModel.objects.get(username=identifier)
            except UserModel.DoesNotExist:
                try:
                    user = UserModel.objects.get(email=identifier)
                except UserModel.DoesNotExist:
                    return render(request, 'mobile/forgot_password.html', {
                        'step': 'verify', 'identifier': identifier,
                        'error': 'PIN invalido ou expirado.',
                    })

            resets = PRR.objects.filter(
                user=user, used=False, expires_at__gt=tz.now(),
            ).order_by('-expires_at')

            reset = None
            for r in resets:
                if r.check_pin(pin):
                    reset = r
                    break

            if not reset:
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
            from app.apps.notifications.models import (
                PasswordResetRequest as PRR,
            )
            from django.utils.crypto import get_random_string
            from django.utils import timezone as tz
            from datetime import timedelta as td

            user = None
            try:
                user = UserModel.objects.get(username=identifier)
            except UserModel.DoesNotExist:
                try:
                    user = UserModel.objects.get(email=identifier)
                except UserModel.DoesNotExist:
                    pass

            if user and user.seller_profile:
                pin = get_random_string(length=6, allowed_chars='0123456789')
                reset = PRR(
                    user=user,
                    expires_at=tz.now() + td(minutes=10),
                )
                reset.set_pin(pin)
                reset.save()

                try:
                    from app.apps.notifications.models import Notification
                    from app.apps.notifications.tasks import (
                        send_whatsapp_notification,
                    )
                    seller = user.seller_profile
                    if seller and seller.phone:
                        notif = Notification.objects.create(
                            tenant=seller.tenant, seller=seller,
                            event_type='seller_credentials',
                            channel='whatsapp',
                            recipient=seller.phone,
                            message_body=(
                                f'Seu PIN de recuperacao de senha Mérito: '
                                f'{pin}. Valido por 10 minutos.'
                            ),
                        )
                        send_whatsapp_notification.delay(notif.uuid)
                except Exception:
                    logger.error(
                        'Failed to send forgot-password notification to user %s',
                        user.id, exc_info=True
                    )

            return render(request, 'mobile/forgot_password.html', {
                'step': 'verify', 'identifier': identifier,
                'message': (
                    'Se o usuario existir, um PIN foi enviado via WhatsApp.'
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
            status='ATIVA',
            sale_date__year=today.year,
            sale_date__month=today.month,
        ).aggregate(total=Sum('amount'))['total'] or 0

        month_link_total = Sale.objects.filter(
            seller=seller,
            origin=Sale.Origin.LINK,
            status='ATIVA',
            sale_date__year=today.year,
            sale_date__month=today.month,
        ).aggregate(total=Sum('amount'))['total'] or 0

        from app.apps.commissions.services import (
            calculate_estimated_commission,
        )

        period = CommissionPeriod.objects.filter(
            tenant=seller.tenant,
            month=today.month,
            year=today.year,
        ).first()

        sc = SellerCommission.objects.filter(
            seller=seller,
            period__month=today.month,
            period__year=today.year,
        ).first()

        sc_status = sc.status if sc else None
        is_editable = sc.is_editable if sc else True

        if sc_status in (SellerCommission.Status.ABERTA, SellerCommission.Status.REABERTA):
            comissao_valor, _ = calculate_estimated_commission(
                seller, today.month, today.year,
            )
            comissao_label = 'Estimada'
        elif sc_status == SellerCommission.Status.FECHADA:
            comissao_valor = sc.amount_due
            comissao_label = 'Fechada'
        elif sc_status == SellerCommission.Status.PAGA:
            comissao_valor = sc.amount_due
            comissao_label = 'Paga'
        elif sc_status == SellerCommission.Status.AJUSTADA:
            comissao_valor = sc.amount_due
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

        combined_month_total = month_total + month_link_total
        goal, goal_progress, goal_remaining = get_seller_goal_context(
            seller, today, combined_month_total,
        )

        missing_past_days = []
        has_missing_past_days = False
        if is_editable and period:
            from app.apps.commissions.services import get_missing_days_before_today
            missing_past_days = get_missing_days_before_today(
                seller, today.month, today.year,
            )
            has_missing_past_days = len(missing_past_days) > 0

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
            'missing_past_days': missing_past_days,
            'has_missing_past_days': has_missing_past_days,
            'goal': goal,
            'goal_progress': goal_progress,
            'goal_remaining': goal_remaining,
            'combined_month_total': combined_month_total,
            'current_year': today.year,
            'current_month': today.month,
        })
    except Exception as e:
        logger.exception("Erro ao carregar mobile_home")
        return render(request, 'mobile/home.html', {
            'error': 'Ocorreu um erro inesperado. Tente novamente.',
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
            if amount_cents > 10_000_000:
                raise ValueError('Valor maximo e R$ 100.000,00.')

            sale_date = (
                date.fromisoformat(sale_date_str)
                if sale_date_str else timezone.localdate()
            )

            today = timezone.localdate()
            if sale_date > today:
                raise ValueError(
                    'Nao e possivel lancar vendas em data futura.',
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
                from app.apps.commissions.services import ensure_seller_commission
                ensure_seller_commission(seller, sale_date)

            success = True
            last_amount = amount_cents
            was_update = existing is not None

            from app.apps.accounts.models import mark_onboarding_step
            mark_onboarding_step(seller.tenant, 'step_first_sale')
        except ValueError as e:
            error = str(e)
            last_amount = 0
            was_update = False
        except Exception as e:
            logger.exception("Erro inesperado em mobile_lancar_venda")
            error = 'Ocorreu um erro inesperado. Tente novamente.'
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

    today = timezone.localdate()
    reference_date = existing_sale.sale_date if existing_sale else today
    sc = SellerCommission.objects.filter(
        seller=seller,
        period__month=reference_date.month,
        period__year=reference_date.year,
    ).first()
    is_editable = sc.is_editable if sc else True
    sc_status = sc.status if sc else None

    return render(request, 'mobile/lancar_venda.html', {
        'seller': seller,
        'success': success,
        'error': error,
        'existing_sale': existing_sale,
        'last_amount': last_amount if success else 0,
        'was_update': was_update if success else False,
        'is_editable': is_editable,
        'seller_commission_status': sc_status,
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

    from app.apps.commissions.models import SellerCommission

    locked_months = set(
        SellerCommission.objects.filter(
            seller=seller,
            status__in=[
                SellerCommission.Status.FECHADA,
                SellerCommission.Status.PAGA,
                SellerCommission.Status.AJUSTADA,
                SellerCommission.Status.CANCELADA,
            ],
        ).values_list('period__month', 'period__year')
    )

    today = timezone.localdate()
    sales_data = []
    for s in sales:
        is_locked = (s.sale_date.month, s.sale_date.year) in locked_months
        sales_data.append({
            'uuid': str(s.uuid),
            'amount': s.amount,
            'notes': s.notes or '',
            'origin': s.origin,
            'origin_display': s.get_origin_display(),
            'status': s.status,
            'date': s.sale_date.strftime('%d/%m/%Y'),
            'date_iso': s.sale_date.isoformat(),
            'canDelete': s.origin == Sale.Origin.MANUAL and not is_locked,
            'canEdit': s.origin == Sale.Origin.MANUAL and not is_locked,
        })

    return render(request, 'mobile/minhas_vendas.html', {
        'seller': seller,
        'sales': sales,
        'sales_json': sales_data,
        'current_month': today.month,
        'current_year': today.year,
        'months': [
            {'value': i, 'label': f'{i:02d}'} for i in range(1, 13)
        ],
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
        status='ATIVA',
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0

    from app.apps.commissions.services import calculate_estimated_commission

    comissao_estimada, _ = calculate_estimated_commission(
        seller, today.month, today.year,
    )

    commissions = SellerCommission.objects.filter(
        seller=seller,
    ).select_related('period').order_by('-period__year', '-period__month')

    commissions_data = []
    has_current_month_sc = False
    for sc in commissions:
        if sc.period.status == CommissionPeriod.Status.ABERTA:
            est, total_est = calculate_estimated_commission(
                seller, sc.period.month, sc.period.year,
            )
            sc.total_sold_amount = total_est
            sc.commission_amount = est
        if sc.period.month == today.month and sc.period.year == today.year:
            has_current_month_sc = True
        commissions_data.append(sc)

    current_estimate = None
    if not has_current_month_sc:
        est, total_est = calculate_estimated_commission(
            seller, today.month, today.year,
        )
        current_estimate = {
            'month': today.month,
            'year': today.year,
            'total_sold': total_est,
            'commission': est,
        }

    return render(request, 'mobile/meu_desempenho.html', {
        'seller': seller,
        'month_total': month_total,
        'comissao_estimada': comissao_estimada,
        'commissions': commissions_data,
        'current_month': f'{today.month:02d}/{today.year}',
        'current_estimate': current_estimate,
    })


RANKING_TIPS = [
    'Lance suas vendas todos os dias — dias sem lancamento derrubam sua posicao.',
    'Ofereca o link de pagamento para fechar clientes indecisos na hora.',
    'Venda adicionais: um item a mais por atendimento muda seu total do mes.',
    'Comece o dia conferindo sua meta — quem acompanha, bate.',
    'Cliente atendido bem volta. Recompra tambem conta para seu ranking.',
    'Registre a venda na hora, no balcao. Depois a memoria falha.',
]


def get_seller_goal_context(seller, today, combined_month_total):
    from app.apps.sellers.models import SellerGoal
    goal = SellerGoal.objects.filter(
        seller=seller, month=today.month, year=today.year,
    ).first()
    if not goal:
        return None, None, None
    return goal, goal.progress_percent, max(0, goal.target_amount - combined_month_total)


def _get_seller_profile(request):
    try:
        return request.user.seller_profile
    except Exception:
        return None


@login_required
def mobile_ranking(request):
    seller = _get_seller_profile(request)
    if not seller:
        return redirect('dashboard:mobile_home')

    today = timezone.localdate()
    from app.apps.sales.models import Sale
    from django.db.models import Sum
    from app.apps.commissions.services import calculate_estimated_commission

    ranking_qs = Sale.objects.filter(
        tenant=seller.tenant,
        status='ATIVA',
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).values('seller__uuid', 'seller__name').annotate(
        total=Sum('amount'),
    ).order_by('-total')

    ranking = list(ranking_qs)
    seller_uuid_str = str(seller.uuid)
    seller_pos = None
    my_total = 0
    for i, r in enumerate(ranking):
        if str(r['seller__uuid']) == seller_uuid_str:
            seller_pos = i + 1
            my_total = r['total']
            break

    visible_to_sellers = seller.tenant.ranking_visible_to_sellers
    total_sellers = len(ranking)

    if visible_to_sellers:
        ranking_display = [
            {
                'name': r['seller__name'],
                'is_me': str(r['seller__uuid']) == seller_uuid_str,
                'total': r['total'] if str(r['seller__uuid']) == seller_uuid_str else None,
            }
            for r in ranking
        ]
    else:
        ranking_display = []

    my_estimated_commission = 0
    try:
        my_estimated_commission, _ = calculate_estimated_commission(
            seller, today.month, today.year,
        )
    except Exception:
        pass

    month_total_manual = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.MANUAL,
        status='ATIVA',
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0
    month_link_total = Sale.objects.filter(
        seller=seller,
        origin=Sale.Origin.LINK,
        status='ATIVA',
        sale_date__year=today.year,
        sale_date__month=today.month,
    ).aggregate(total=Sum('amount'))['total'] or 0
    combined_month_total = month_total_manual + month_link_total

    goal, goal_progress, goal_remaining = get_seller_goal_context(
        seller, today, combined_month_total,
    )

    start = today.toordinal() % len(RANKING_TIPS)
    tips = []
    for j in range(3):
        tips.append(RANKING_TIPS[(start + j) % len(RANKING_TIPS)])

    return render(request, 'mobile/ranking.html', {
        'seller': seller,
        'ranking': ranking_display,
        'seller_pos': seller_pos,
        'my_total': my_total,
        'my_estimated_commission': my_estimated_commission,
        'total_sellers': total_sellers,
        'visible_to_sellers': visible_to_sellers,
        'current_month': f'{today.month:02d}/{today.year}',
        'goal': goal,
        'goal_progress': goal_progress,
        'goal_remaining': goal_remaining,
        'combined_month_total': combined_month_total,
        'tips': tips,
    })


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
        'seller': seller, 'orders_json': orders_data,
    })
