from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from app.apps.accounts.models import User, tenant_has_feature
from app.apps.sellers.models import Seller

from .models import Boleto, DEFAULT_INSTRUCTIONS
from .serializers import BoletoSerializer
from .services import (
    BoletoServiceError, cancel_boleto, lookup_cnpj, save_invoice_files,
)


def _check_role(request, *roles):
    return request.user.is_authenticated and request.user.role in roles


def _feature_enabled(user):
    return bool(user.tenant_id and tenant_has_feature(user.tenant, 'boletos'))


def _form_context(user, serializer=None, boleto=None, manager=False):
    today = timezone.localdate()
    return {
        'serializer': serializer,
        'boleto': boleto,
        'manager': manager,
        'sellers': Seller.objects.filter(
            tenant=user.tenant, is_active=True,
        ).order_by('name') if manager else [],
        'min_due_date': (today + timedelta(days=1)).isoformat(),
        'max_due_date': (today + timedelta(days=180)).isoformat(),
        'default_instructions': DEFAULT_INSTRUCTIONS,
    }


def _create_from_post(request):
    serializer = BoletoSerializer(data=request.POST, context={'request': request})
    if serializer.is_valid():
        return serializer, serializer.save()
    return serializer, None


@login_required
def mobile_boleto_list(request):
    if request.user.role != User.Role.SELLER:
        return redirect('dashboard:home')
    if not _feature_enabled(request.user):
        return render(request, 'mobile/boletos/list.html', {'feature_locked': True})
    try:
        seller = request.user.seller_profile
    except Seller.DoesNotExist:
        return render(request, 'mobile/boletos/list.html', {
            'error': 'Perfil de vendedor nao encontrado.',
        })
    boletos = Boleto.objects.filter(tenant=request.user.tenant, seller=seller)
    status_value = request.GET.get('status', '').upper()
    if status_value:
        boletos = boletos.filter(status=status_value)
    search = request.GET.get('search', '').strip()
    if search:
        boletos = boletos.filter(payer_name__icontains=search)
    return render(request, 'mobile/boletos/list.html', {
        'boletos': boletos[:100],
        'status_choices': Boleto.Status.choices,
        'status_filter': status_value,
        'search': search,
    })


@login_required
def mobile_boleto_new(request):
    if request.user.role != User.Role.SELLER:
        return redirect('dashboard:home')
    if not _feature_enabled(request.user):
        return render(request, 'mobile/boletos/new.html', {'feature_locked': True})
    serializer = None
    boleto = None
    if request.method == 'POST':
        serializer, boleto = _create_from_post(request)
    context = _form_context(request.user, serializer, boleto)
    return render(request, 'mobile/boletos/new.html', context)


@login_required
def manager_boleto_list(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')
    if not _feature_enabled(request.user):
        return render(request, 'dashboard/gestor/boletos/list.html', {
            'feature_locked': True,
        })
    today = timezone.localdate()
    queryset = Boleto.objects.filter(tenant=request.user.tenant).select_related(
        'seller', 'launched_sale',
    )
    status_value = request.GET.get('status', '').upper()
    seller_uuid = request.GET.get('seller', '')
    due_after = request.GET.get('due_after', '')
    due_before = request.GET.get('due_before', '')
    search = request.GET.get('search', '').strip()
    if status_value:
        queryset = queryset.filter(status=status_value)
    if seller_uuid:
        queryset = queryset.filter(seller_id=seller_uuid)
    if due_after:
        queryset = queryset.filter(due_date__gte=due_after)
    if due_before:
        queryset = queryset.filter(due_date__lte=due_before)
    if search:
        from django.db.models import Q
        digits = ''.join(filter(str.isdigit, search))
        search_query = Q(payer_name__icontains=search)
        if digits:
            search_query |= Q(payer_document__icontains=digits)
        queryset = queryset.filter(search_query)
    base = Boleto.objects.filter(tenant=request.user.tenant)

    def total(qs, field='amount_cents'):
        return qs.aggregate(value=Sum(field))['value'] or 0

    boleto_rows = list(queryset[:250])
    stale_cutoff = timezone.now() - timedelta(days=2)
    for boleto in boleto_rows:
        boleto.awaiting_long = bool(
            boleto.status == Boleto.Status.PAGO
            and not boleto.launched_sale_id
            and boleto.paid_at
            and boleto.paid_at <= stale_cutoff
        )
    return render(request, 'dashboard/gestor/boletos/list.html', {
        'boletos': boleto_rows,
        'sellers': Seller.objects.filter(tenant=request.user.tenant, is_active=True),
        'status_choices': Boleto.Status.choices,
        'status_filter': status_value,
        'seller_filter': seller_uuid,
        'due_after': due_after,
        'due_before': due_before,
        'search': search,
        'a_receber_cents': total(base.filter(status=Boleto.Status.PENDENTE)),
        'vencendo_7d_cents': total(base.filter(
            status=Boleto.Status.PENDENTE,
            due_date__gte=today,
            due_date__lte=today + timedelta(days=7),
        )),
        'vencidos_cents': total(base.filter(status=Boleto.Status.VENCIDO)),
        'pagos_mes_cents': total(base.filter(
            status=Boleto.Status.PAGO,
            paid_at__year=today.year,
            paid_at__month=today.month,
        ), 'paid_amount_cents'),
    })


@login_required
def manager_boleto_detail(request, boleto_uuid):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')
    if not _feature_enabled(request.user):
        return redirect('dashboard:gestor_boletos')
    boleto = get_object_or_404(
        Boleto.objects.select_related('seller', 'created_by', 'launched_sale'),
        tenant=request.user.tenant,
        uuid=boleto_uuid,
    )
    return render(request, 'dashboard/gestor/boletos/detail.html', {
        'boleto': boleto,
    })


@login_required
def manager_boleto_new(request):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')
    if not _feature_enabled(request.user):
        return render(request, 'dashboard/gestor/boletos/new.html', {
            'feature_locked': True,
        })
    serializer = None
    boleto = None
    if request.method == 'POST':
        serializer, boleto = _create_from_post(request)
    return render(
        request,
        'dashboard/gestor/boletos/new.html',
        _form_context(request.user, serializer, boleto, manager=True),
    )


@login_required
def manager_boleto_cancel(request, boleto_uuid):
    if request.method != 'POST' or not _check_role(
        request, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:gestor_boletos')
    boleto = get_object_or_404(
        Boleto, tenant=request.user.tenant, uuid=boleto_uuid,
    )
    try:
        cancel_boleto(boleto, request.user)
    except BoletoServiceError:
        pass
    return redirect('dashboard:gestor_boletos')


@login_required
def manager_boleto_resend(request, boleto_uuid):
    if request.method != 'POST' or not _check_role(
        request, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:gestor_boletos')
    boleto = get_object_or_404(
        Boleto, tenant=request.user.tenant, uuid=boleto_uuid,
    )
    if boleto.payer_email:
        from .tasks import send_boleto_email
        send_boleto_email.delay(str(boleto.uuid), 'created', True)
    return redirect('dashboard:gestor_boletos')


@login_required
def manager_boleto_invoice_upload(request, boleto_uuid):
    if request.method != 'POST' or not _check_role(
        request, User.Role.ADMIN, User.Role.MANAGER,
    ):
        return redirect('dashboard:gestor_boletos')
    boleto = get_object_or_404(
        Boleto, tenant=request.user.tenant, uuid=boleto_uuid,
    )
    try:
        boleto = save_invoice_files(
            boleto,
            request.user,
            pdf=request.FILES.get('invoice_pdf'),
            xml=request.FILES.get('invoice_xml'),
        )
        if request.POST.get('send_email') and boleto.payer_email:
            from .tasks import send_boleto_email
            send_boleto_email.delay(str(boleto.uuid), 'invoice', True)
    except BoletoServiceError as exc:
        return render(request, 'dashboard/gestor/boletos/detail.html', {
            'boleto': boleto,
            'invoice_error': str(exc),
        }, status=400)
    return redirect('dashboard:gestor_boleto_detalhe', boleto_uuid=boleto.uuid)


@login_required
def manager_boleto_invoice_download(request, boleto_uuid, kind):
    if not _check_role(request, User.Role.ADMIN, User.Role.MANAGER):
        return redirect('dashboard:home')
    boleto = get_object_or_404(
        Boleto, tenant=request.user.tenant, uuid=boleto_uuid,
    )
    field = boleto.invoice_pdf if kind == 'pdf' else boleto.invoice_xml if kind == 'xml' else None
    if not field:
        return JsonResponse({'detail': 'Arquivo nao encontrado.'}, status=404)
    content_type = 'application/pdf' if kind == 'pdf' else 'application/xml'
    return FileResponse(
        field.open('rb'),
        content_type=content_type,
        as_attachment=True,
        filename=f'nota-fiscal-{boleto.uuid}.{kind}',
    )


@login_required
def cnpj_lookup(request, cnpj):
    if request.user.role not in (User.Role.SELLER, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'detail': 'Acesso nao permitido.'}, status=403)
    if not _feature_enabled(request.user):
        return JsonResponse({'detail': 'Recurso indisponivel no plano.'}, status=403)
    try:
        return JsonResponse(lookup_cnpj(cnpj))
    except (ValueError, BoletoServiceError) as exc:
        return JsonResponse({'detail': str(exc)}, status=400)


@login_required
def cep_lookup(request, cep):
    if request.user.role not in (User.Role.SELLER, User.Role.ADMIN, User.Role.MANAGER):
        return JsonResponse({'detail': 'Acesso nao permitido.'}, status=403)
    if not _feature_enabled(request.user):
        return JsonResponse({'detail': 'Recurso indisponivel no plano.'}, status=403)
    from app.apps.freight.services import ViaCepClient
    info = ViaCepClient().get_cep_info(cep)
    if not info:
        return JsonResponse({'detail': 'CEP nao encontrado.'}, status=404)
    return JsonResponse({
        'payer_zip_code': info.cep,
        'payer_street': info.street,
        'payer_neighborhood': info.neighborhood,
        'payer_city': info.city,
        'payer_state': info.state,
    })
