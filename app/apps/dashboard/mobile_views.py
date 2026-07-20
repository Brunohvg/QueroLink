import json
import logging

from django.shortcuts import render, redirect
from django.http import Http404
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

from app.apps.accounts.models import User, tenant_has_feature
from app.apps.sales.models import Sale
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.audit.utils import log_action
from app.apps.receivables.models import Boleto
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

MONTH_NAMES_PT = [
    '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
]


def mobile_login(request):
    if (
        request.user.is_authenticated
        and request.user.role == User.Role.SELLER
    ):
        return redirect('dashboard:mobile_home')
    orphan_msg = request.GET.get('msg', '')
    return render(request, 'mobile/login.html', {'orphan_msg': orphan_msg})


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
                pin = get_random_string(length=8, allowed_chars='0123456789')
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
        if hasattr(seller, 'status_code'):
            return seller
        if not seller:
            return render(request, 'mobile/home.html', {
                'error': 'Perfil de vendedor nao encontrado.',
            })

        today = timezone.localdate()
        today_manual = Sale.objects.filter(
            seller=seller, origin=Sale.Origin.MANUAL, status='ATIVA',
            sale_date=today,
        ).first()
        today_total = today_manual.amount if today_manual else 0
        has_entry_today = today_manual is not None
        from app.apps.sellers.models import SellerDayJustification
        has_justification_today = SellerDayJustification.objects.filter(
            tenant=seller.tenant, seller=seller, date=today,
        ).exists()

        from app.apps.commissions.services import (
            calculate_estimated_commission_for_period,
            get_current_period,
        )

        period = get_current_period(seller.tenant)
        no_current_period_message = None
        if not period:
            no_current_period_message = 'Nenhuma competencia aberta para a data atual.'
            month_total = 0
            month_link_total = 0
            sc = None
        else:
            month_total = Sale.objects.filter(
                seller=seller,
                origin__in=Sale.COMMISSION_ORIGINS,
                status='ATIVA',
                sale_date__gte=period.start_date,
                sale_date__lte=period.end_date,
            ).aggregate(total=Sum('amount'))['total'] or 0

            month_link_total = 0

            sc = SellerCommission.objects.filter(
                seller=seller,
                period=period,
            ).first()

        sc_status = sc.status if sc else None
        is_editable = sc.is_editable if sc else True

        if sc_status in (SellerCommission.Status.ABERTA, SellerCommission.Status.REABERTA):
            comissao_valor, _ = calculate_estimated_commission_for_period(seller, period)
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
            if not sc and period:
                comissao_valor, _ = calculate_estimated_commission_for_period(seller, period)
                comissao_label = 'Estimada'
            else:
                comissao_valor = sc.commission_amount if sc else 0
                comissao_label = ''

        periodo_status = period.status if period else None

        combined_month_total = month_total
        goal, goal_progress, goal_remaining = get_seller_goal_context(
            seller, today, combined_month_total,
        )

        missing_past_days = []
        has_missing_past_days = False
        if is_editable and period:
            from app.apps.commissions.services import get_missing_days_for_period
            missing_past_days = get_missing_days_for_period(seller, period)
            # LOTE 6: dia com justificativa nao conta como pendente.
            if missing_past_days:
                from app.apps.sellers.models import SellerDayJustification
                justified = set(
                    SellerDayJustification.objects.filter(
                        tenant=seller.tenant, seller=seller,
                        date__gte=period.start_date,
                        date__lte=period.end_date,
                    ).values_list('date', flat=True)
                )
                if justified:
                    missing_past_days = [
                        d for d in missing_past_days if d not in justified
                    ]
            has_missing_past_days = len(missing_past_days) > 0

        from app.apps.accounts.models import is_working_day
        is_working_day_today = is_working_day(seller.tenant, today)

        return render(request, 'mobile/home.html', {
            'seller': seller,
            'today_total': today_total,
            'has_entry_today': has_entry_today,
            'has_justification_today': has_justification_today,
            'has_day_resolved_today': (
                has_entry_today or has_justification_today
            ),
            'month_total': month_total,
            'month_link_total': month_link_total,
            'comissao_estimada': comissao_valor,
            'comissao_label': comissao_label,
            'periodo_status': periodo_status,
            'periodo_label': period.display_label if period else '',
            'periodo_range': (
                f'{period.start_date.strftime("%d/%m/%Y")} a {period.end_date.strftime("%d/%m/%Y")}'
                if period else ''
            ),
            'no_current_period_message': no_current_period_message,
            'is_editable': is_editable,
            'seller_commission_status': sc_status,
            'missing_past_days': missing_past_days,
            'has_missing_past_days': has_missing_past_days,
            'goal': goal,
            'goal_progress': goal_progress,
            'goal_remaining': goal_remaining,
            'combined_month_total': combined_month_total,
            'current_year': period.year if period else today.year,
            'current_month': period.month if period else today.month,
            'period_uuid': str(period.uuid) if period else '',
            'is_working_day_today': is_working_day_today,
        })
    except Exception as e:
        logger.exception("Erro ao carregar mobile_home")
        return render(request, 'mobile/home.html', {
            'error': 'Ocorreu um erro inesperado. Tente novamente.',
        })


@login_required
def mobile_lancar_venda(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
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

            # LOTE 2/8: dia justificado nao pode receber venda silenciosamente.
            # O vendedor nao gerencia justificativa: deve procurar o gestor.
            from app.apps.sellers.services import has_active_justification
            if has_active_justification(seller.tenant, seller, sale_date):
                raise ValueError(
                    'Este dia possui uma justificativa registrada pelo gestor. '
                    'Procure o gestor para ajustar.'
                )

            existing = Sale.objects.filter(
                seller=seller,
                origin__in=Sale.COMMISSION_ORIGINS,
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
                    origin__in=Sale.COMMISSION_ORIGINS,
                    sale_date=query_date,
                ).first()
            except (ValueError, Exception):
                pass

    today = timezone.localdate()
    reference_date = existing_sale.sale_date if existing_sale else today
    from app.apps.commissions.services import resolve_period_for_date
    period = resolve_period_for_date(seller.tenant, reference_date)
    sc = SellerCommission.objects.filter(seller=seller, period=period).first() if period else None
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
    """Vendas do vendedor navegadas por COMPETENCIA (competence-first).

    A competencia (nao o mes-calendario) e a navegacao principal: a tela
    responde "quais vendas pertencem a competencia que define meu pagamento?".

    - Seletor principal: competencias do vendedor (com venda ou
      SellerCommission), CANCELADAS excluidas. Default: competencia
      operacional atual; senao a mais recente com vendas; senao vazio.
    - Ao selecionar uma competencia o backend retorna APENAS as vendas do
      range (period.start_date..period.end_date) -- nunca o historico completo.
    - Semanas recalculadas a partir do inicio da competencia
      (week_index = floor((sale_date - start)/7) + 1), datas civis sem fuso.
    - Filtro secundario por mes (apenas meses que interceptam a competencia)
      reduz visualmente, jamais carrega vendas de outra competencia.
    - Vendas sem competencia ficam num estado separado (?period=sem-competencia)
      e nunca entram no total da competencia.

    Regras de edicao/exclusao (canEdit/canDelete) permanecem 100% delegadas a
    regra CENTRAL `build_sale_change_permission_resolver` (mesma logica de
    `validate_sale_can_be_changed`, sem query por venda):
    - MANUAL + competencia editavel (ABERTA/REABERTA): pode editar/excluir.
    - IMPORTADA / ESTORNADA: somente leitura.
    - competencia FECHADA/PAGA/CANCELADA bloqueia.
    - SellerCommission FECHADA/PAGA/AJUSTADA/CANCELADA bloqueia.
    - Venda MANUAL sem competencia que a contenha: permanece editavel.
    """
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return render(request, 'mobile/minhas_vendas.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    from app.apps.sales.models import SaleChangeLog
    from django.db.models import Count, Q
    from app.apps.commissions.services import (
        build_sale_change_permission_resolver,
    )

    tenant = seller.tenant

    # Todas as competencias do tenant carregadas UMA vez. Inclui CANCELADAS
    # apenas para deteccao de orfaos e permissao; o seletor as exclui.
    all_periods = list(
        CommissionPeriod.objects.filter(tenant=tenant).order_by('start_date')
    )

    def _resolve_competencia(sale_date, include_cancelled=True):
        for p in all_periods:
            if p.start_date <= sale_date <= p.end_date:
                if (
                    not include_cancelled
                    and p.status == CommissionPeriod.Status.CANCELADA
                ):
                    continue
                return p
        return None

    # Datas distintas com venda (1 query) -> deriva competencias com atividade
    # e detecta vendas orfas, sem carregar todas as vendas historicas no JSON.
    sale_days = list(
        Sale.objects.filter(
            seller=seller,
            origin__in=Sale.COMMISSION_ORIGINS,
        ).dates('sale_date', 'day')
    )
    active_period_ids = set()
    has_orphan = False
    for d in sale_days:
        if _resolve_competencia(d, include_cancelled=True) is None:
            has_orphan = True
        p_active = _resolve_competencia(d, include_cancelled=False)
        if p_active:
            active_period_ids.add(p_active.uuid)

    # LOTE 3 - IDs de SellerCommission do vendedor em UMA unica query.
    sc_period_ids = set(
        SellerCommission.objects.filter(seller=seller)
        .values_list('period_id', flat=True)
    )

    # LOTE 2 - competencia operacional atual com verificacao de sobreposicao
    # (mesma regra central de get_default_period): 0 periodos nao cancelados
    # cobrindo hoje -> None; 1 -> esse; >1 -> erro controlado (NUNCA escolher
    # silenciosamente a primeira). Computado a partir de all_periods (sem
    # query extra).
    today = timezone.localdate()
    covering_today = [
        p for p in all_periods
        if p.status != CommissionPeriod.Status.CANCELADA
        and p.start_date <= today <= p.end_date
    ]
    if len(covering_today) > 1:
        logger.error(
            'Competencias sobrepostas cobrindo hoje para tenant %s: %s',
            tenant.pk, [str(p.uuid) for p in covering_today],
        )
        return render(request, 'mobile/minhas_vendas.html', {
            'error': (
                'Ha competencias sobrepostas cobrindo a data atual. '
                'Contate o gestor para corrigir os periodos.'
            ),
            'competence_options': [],
            'selected_competence': None,
            'selected_period_uuid': '',
            'show_unassigned': False,
            'has_orphan': False,
            'unassigned_count': 0,
            'month_options': [],
            'weeks_json': [],
            'sales_json': [],
            'competence_total': 0,
            'competence_count': 0,
        })
    current_period = covering_today[0] if covering_today else None

    # LOTE 1/3 - seletor principal: competencias nao canceladas que tenham
    # venda do vendedor OU SellerCommission do vendedor OU sejam a operacional
    # atual. Ordenado por start_date desc.
    competence_periods = [
        p for p in all_periods
        if p.status != CommissionPeriod.Status.CANCELADA
        and (
            p.uuid in active_period_ids
            or p.uuid in sc_period_ids
            or (current_period and p.uuid == current_period.uuid)
        )
    ]
    competence_periods.sort(key=lambda p: p.start_date, reverse=True)

    # LOTE 1 - resolucao da competencia selecionada.
    # UUID explicito NUNCA cai em fallback: se nao existir / for invalido /
    # de outro tenant / cancelado / inacessivel ao vendedor -> Http404.
    period_param = (request.GET.get('period') or '').strip()
    selected = None
    show_unassigned = False

    if period_param == 'sem-competencia':
        show_unassigned = True
    elif period_param:
        selected = next(
            (p for p in competence_periods if str(p.uuid) == period_param),
            None,
        )
        if selected is None:
            raise Http404('Competencia nao encontrada ou inacessivel.')
    else:
        # Default 1: operacional atual (se acessivel ao vendedor).
        if current_period and any(
            p.uuid == current_period.uuid for p in competence_periods
        ):
            selected = next(
                p for p in competence_periods
                if p.uuid == current_period.uuid
            )
        else:
            # Default 2: mais recente com atividade (venda ou SellerCommission).
            selected = next(
                (
                    p for p in competence_periods
                    if p.uuid in active_period_ids or p.uuid in sc_period_ids
                ),
                None,
            )
        # Default 3: estado vazio (selected permanece None).

    competence_options = [
        {
            'uuid': str(p.uuid),
            'label': p.display_label,
            'range': (
                f'{p.start_date.strftime("%d/%m")} a '
                f'{p.end_date.strftime("%d/%m")}'
            ),
            'status_display': p.get_status_display(),
            'is_current': bool(
                current_period and p.uuid == current_period.uuid
            ),
        }
        for p in competence_periods
    ]

    # Regra central de edicao/exclusao em lote (SellerCommission 1 query).
    can_change = build_sale_change_permission_resolver(
        seller, periods=all_periods,
    )

    # LOTE 8 - backend retorna APENAS as vendas do range selecionado (ou os
    # orfaos), nunca o historico completo do vendedor.
    if show_unassigned:
        covered_any = Q()
        for p in all_periods:
            covered_any |= Q(
                sale_date__gte=p.start_date, sale_date__lte=p.end_date,
            )
        sales_qs = Sale.objects.filter(
            seller=seller,
            origin__in=Sale.COMMISSION_ORIGINS,
        )
        if covered_any:
            sales_qs = sales_qs.exclude(covered_any)
        sales_list = list(sales_qs.order_by('-sale_date', '-created_at'))
    elif selected is not None:
        sales_list = list(
            Sale.objects.filter(
                seller=seller,
                origin__in=Sale.COMMISSION_ORIGINS,
                sale_date__gte=selected.start_date,
                sale_date__lte=selected.end_date,
            ).order_by('-sale_date', '-created_at')
        )
    else:
        sales_list = []

    log_counts = dict(
        SaleChangeLog.objects.filter(
            sale__in=[s.pk for s in sales_list],
        ).values('sale_id').annotate(
            count=Count('uuid'),
        ).values_list('sale_id', 'count')
    )

    # LOTE 3 - semanas recalculadas a partir do INICIO da competencia.
    weeks_meta = []
    if selected is not None:
        total_days = (selected.end_date - selected.start_date).days + 1
        num_weeks = (total_days + 6) // 7
        for i in range(num_weeks):
            w_start = selected.start_date + timedelta(days=i * 7)
            w_end = min(
                selected.start_date + timedelta(days=i * 7 + 6),
                selected.end_date,
            )
            weeks_meta.append({
                'index': i + 1,
                'label': f'Semana {i + 1}',
                'range': (
                    f'{w_start.strftime("%d/%m")} a '
                    f'{w_end.strftime("%d/%m")}'
                ),
            })
    elif show_unassigned:
        weeks_meta = [{'index': 1, 'label': 'Sem competência', 'range': ''}]

    competence_total = 0
    sales_data = []
    for s in sales_list:
        allowed = can_change(s)
        if selected is not None:
            week_index = (s.sale_date - selected.start_date).days // 7 + 1
        else:
            week_index = 1
        sales_data.append({
            'uuid': str(s.uuid),
            'amount': s.amount,
            'notes': s.notes or '',
            'origin': s.origin,
            'origin_display': s.get_origin_display(),
            'status': s.status,
            'date': s.sale_date.strftime('%d/%m/%Y'),
            'date_iso': s.sale_date.isoformat(),
            'month_key': s.sale_date.strftime('%Y-%m'),
            'week_index': week_index,
            'canDelete': allowed,
            'canEdit': allowed,
            'change_log_count': log_counts.get(s.uuid, 0),
        })
        if s.status == 'ATIVA':
            competence_total += s.amount

    # LOTE 4 - filtro secundario: apenas meses que interceptam a competencia.
    month_options = []
    if selected is not None:
        y, m = selected.start_date.year, selected.start_date.month
        ey, em = selected.end_date.year, selected.end_date.month
        while (y, m) <= (ey, em):
            month_options.append({
                'key': f'{y:04d}-{m:02d}',
                'label': f'{MONTH_NAMES_PT[m]}/{y}',
            })
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1

    # LOTE 6 - contagem de vendas orfas (nunca somadas na competencia).
    unassigned_count = 0
    if has_orphan:
        covered_any = Q()
        for p in all_periods:
            covered_any |= Q(
                sale_date__gte=p.start_date, sale_date__lte=p.end_date,
            )
        orphan_qs = Sale.objects.filter(
            seller=seller,
            origin__in=Sale.COMMISSION_ORIGINS,
        )
        if covered_any:
            orphan_qs = orphan_qs.exclude(covered_any)
        unassigned_count = orphan_qs.count()

    if show_unassigned:
        selected_period_uuid = 'sem-competencia'
    elif selected is not None:
        selected_period_uuid = str(selected.uuid)
    else:
        selected_period_uuid = ''

    selected_competence = None
    if selected is not None:
        selected_competence = {
            'uuid': str(selected.uuid),
            'display_label': selected.display_label,
            'start_iso': selected.start_date.isoformat(),
            'end_iso': selected.end_date.isoformat(),
            'range_full': (
                f'{selected.start_date.strftime("%d/%m/%Y")} a '
                f'{selected.end_date.strftime("%d/%m/%Y")}'
            ),
            'status': selected.status,
            'status_display': selected.get_status_display(),
        }

    return render(request, 'mobile/minhas_vendas.html', {
        'seller': seller,
        'competence_options': competence_options,
        'selected_competence': selected_competence,
        'selected_period_uuid': selected_period_uuid,
        'show_unassigned': show_unassigned,
        'has_orphan': has_orphan,
        'unassigned_count': unassigned_count,
        'month_options': month_options,
        'weeks_json': weeks_meta,
        'sales_json': sales_data,
        'competence_total': competence_total,
        'competence_count': len(sales_data),
    })


@login_required
def mobile_meu_desempenho(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return render(request, 'mobile/meu_desempenho.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    today = timezone.localdate()
    from app.apps.commissions.services import (
        calculate_estimated_commission_for_period,
        get_current_period,
    )

    current_period = get_current_period(seller.tenant)
    if current_period:
        month_total = Sale.objects.filter(
            seller=seller,
            origin__in=Sale.COMMISSION_ORIGINS,
            status='ATIVA',
            sale_date__gte=current_period.start_date,
            sale_date__lte=current_period.end_date,
        ).aggregate(total=Sum('amount'))['total'] or 0
        comissao_estimada, _ = calculate_estimated_commission_for_period(
            seller, current_period,
        )
    else:
        month_total = 0
        comissao_estimada = 0

    commissions = SellerCommission.objects.filter(
        seller=seller,
    ).select_related('period').order_by('-period__start_date')

    commissions_data = []
    has_current_month_sc = False
    for sc in commissions:
        if sc.period.status == CommissionPeriod.Status.ABERTA:
            est, total_est = calculate_estimated_commission_for_period(seller, sc.period)
            sc.total_sold_amount = total_est
            sc.commission_amount = est
        if current_period and sc.period_id == current_period.uuid:
            has_current_month_sc = True
        commissions_data.append(sc)

    current_estimate = None
    if current_period and not has_current_month_sc:
        est, total_est = calculate_estimated_commission_for_period(
            seller, current_period,
        )
        current_estimate = {
            'month': current_period.month,
            'year': current_period.year,
            'label': current_period.display_label,
            'total_sold': total_est,
            'commission': est,
        }

    return render(request, 'mobile/meu_desempenho.html', {
        'seller': seller,
        'month_total': month_total,
        'comissao_estimada': comissao_estimada,
        'commissions': commissions_data,
        'current_month': current_period.display_label if current_period else '',
        'current_estimate': current_estimate,
        'no_current_period_message': (
            'Nenhuma competencia aberta para a data atual.'
            if not current_period else None
        ),
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
    progress = 0
    if goal.target_amount > 0:
        progress = min(100, int(combined_month_total * 100 / goal.target_amount))
    return goal, progress, max(0, goal.target_amount - combined_month_total)


def _get_seller_profile(request):
    try:
        return request.user.seller_profile
    except Exception:
        if request.user.is_authenticated and request.user.role == User.Role.SELLER:
            auth_logout(request)
            from django.urls import reverse
            from urllib.parse import urlencode
            return redirect(
                reverse('dashboard:mobile_login')
                + '?' + urlencode({'msg': 'Este acesso foi desativado. Fale com seu gestor para receber novas credenciais.'})
            )
        return None


@login_required
def mobile_ranking(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')

    today = timezone.localdate()
    from app.apps.sales.models import Sale
    from django.db.models import Sum
    from app.apps.commissions.services import (
        calculate_estimated_commission_for_period,
        get_current_period,
    )

    current_period = get_current_period(seller.tenant)
    if not current_period:
        return render(request, 'mobile/ranking.html', {
            'seller': seller,
            'ranking': [],
            'seller_pos': None,
            'my_total': 0,
            'my_estimated_commission': 0,
            'total_sellers': 0,
            'visible_to_sellers': seller.tenant.ranking_visible_to_sellers,
            'current_month': '',
            'goal': None,
            'goal_progress': None,
            'goal_remaining': None,
            'tips': [],
            'no_current_period_message': 'Nenhuma competencia aberta para a data atual.',
        })

    ranking_qs = Sale.objects.filter(
        tenant=seller.tenant,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=current_period.start_date,
        sale_date__lte=current_period.end_date,
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
        my_estimated_commission, _ = calculate_estimated_commission_for_period(
            seller, current_period,
        )
    except Exception:
        pass

    month_total_manual = Sale.objects.filter(
        seller=seller,
        origin__in=Sale.COMMISSION_ORIGINS,
        status='ATIVA',
        sale_date__gte=current_period.start_date,
        sale_date__lte=current_period.end_date,
    ).aggregate(total=Sum('amount'))['total'] or 0
    month_link_total = 0
    combined_month_total = month_total_manual

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
        'current_month': current_period.display_label,
        'goal': goal,
        'goal_progress': goal_progress,
        'goal_remaining': goal_remaining,
        'combined_month_total': combined_month_total,
        'tips': tips,
        'periodo_range': (
            f'{current_period.start_date.strftime("%d/%m")} a {current_period.end_date.strftime("%d/%m")}'
        ),
    })


@login_required
def mobile_perfil(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return render(request, 'mobile/perfil.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })

    if request.method == 'POST' and request.POST.get('action') == 'save_cpf':
        if seller.cpf:
            return render(request, 'mobile/perfil.html', {
                'seller': seller,
                'cpf_error': 'CPF ja cadastrado. Para alterar, fale com seu gestor.',
                'cpf_success': False,
            })

        cpf_input = request.POST.get('cpf', '').strip()
        if not cpf_input:
            return render(request, 'mobile/perfil.html', {
                'seller': seller,
                'cpf_error': 'Informe um CPF valido.',
                'cpf_success': False,
            })

        seller.cpf = cpf_input
        try:
            seller.full_clean()
            seller.save()
            log_action(request, 'seller_cpf_self_registered', instance=seller,
                       changes={'cpf': seller.cpf})
            return render(request, 'mobile/perfil.html', {
                'seller': seller,
                'cpf_success': True,
            })
        except ValidationError as e:
            msgs = e.message_dict.get('cpf', ['CPF invalido.'])
            return render(request, 'mobile/perfil.html', {
                'seller': seller,
                'cpf_error': msgs[0] if isinstance(msgs, list) else str(msgs),
                'cpf_success': False,
            })

    return render(request, 'mobile/perfil.html', {
        'seller': seller,
    })


@login_required
def mobile_links(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')
    from app.apps.dashboard.charge_center import build_charge_center
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
            'status_display': o.status_display_pt,
            'link_url': link_url,
            'refusal_reason': refusal,
            'created_at': o.created_at.isoformat(),
        })
    return render(request, 'mobile/links.html', {
        'seller': seller, 'orders_json': orders_data,
    })


@login_required
def mobile_frete(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')
    tenant = request.user.tenant
    from app.apps.freight.services import get_freight_presets
    presets = get_freight_presets(tenant)
    return render(request, 'mobile/frete.html', {
        'seller': seller,
        'store_cep_configured': bool(tenant.store_cep),
        'presets_json': presets,
    })


@login_required
def mobile_boletos(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return redirect('dashboard:mobile_home')

    boletos = Boleto.objects.filter(
        tenant=request.user.tenant, seller=seller,
    ).order_by('-created_at')[:100]

    boletos_data = []
    for b in boletos:
        boletos_data.append({
            'uuid': str(b.uuid),
            'amount_cents': b.amount_cents,
            'status': b.status,
            'status_display': b.get_status_display(),
            'due_date': b.due_date.isoformat(),
            'paid_at': b.paid_at.isoformat() if b.paid_at else None,
            'paid_amount_cents': b.paid_amount_cents,
            'created_at': b.created_at.isoformat(),
        })

    return render(request, 'mobile/boletos/list.html', {
        'seller': seller,
        'boletos_json': boletos_data,
    })


@login_required
def mobile_boleto_new(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return redirect('dashboard:mobile_home')

    return render(request, 'mobile/boletos/new.html', {
        'seller': seller,
    })


@login_required
def mobile_cobrancas(request):
    seller = _get_seller_profile(request)
    if hasattr(seller, 'status_code'):
        return seller
    if not seller:
        return redirect('dashboard:mobile_home')

    from app.apps.orders.models import Order
    from app.apps.accounts.models import tenant_has_feature

    cobrancas, _, boletos_data = build_charge_center(
        request.user.tenant, seller=seller,
    )
    can_create_boletos = tenant_has_feature(request.user.tenant, 'boletos')

    return render(request, 'mobile/cobrancas.html', {
        'seller': seller,
        'cobrancas_json': cobrancas,
        'has_boletos': bool(can_create_boletos or boletos_data),
        'can_create_boletos': can_create_boletos,
    })
