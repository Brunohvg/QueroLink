import logging

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import (
    api_view, permission_classes, throttle_classes,
)
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.apps.accounts.models import (
    User, can_manage_receivable, tenant_has_feature,
)
from app.apps.audit.utils import log_action

from .allocation_services import (
    allocate_paid_boleto as allocate_boleto,
    AllocationDomainError,
)
from .commission_services import apply_commission_impact
from .models import Boleto, ReceivableAllocation, CommissionImpactReview
from .serializers import (
    BoletoListSerializer,
    BoletoDetailSerializer,
    BoletoCreateSerializer,
    AllocationSerializer,
    AllocationCreateSerializer,
    CommissionImpactReviewSerializer,
)
from .services import create_boleto, cancel_boleto, BoletoServiceError
from .throttles import BoletoCreateThrottle, BoletoCancelThrottle


logger = logging.getLogger(__name__)


class BoletoPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class AllocationPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class ReviewPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


def _tenant_enabled(request):
    if not tenant_has_feature(request.user.tenant, 'boletos'):
        return False
    return True


def _is_gestor(user):
    return user.role in (User.Role.ADMIN, User.Role.MANAGER)


def _get_boleto_queryset(user):
    qs = Boleto.objects.filter(tenant=user.tenant)
    if user.role == User.Role.SELLER:
        qs = qs.filter(seller__user=user)
    return qs.select_related('seller', 'created_by')


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BoletoCreateThrottle])
def boleto_list_create(request):
    if not _tenant_enabled(request):
        return Response(
            {'detail': 'Funcionalidade nao disponivel.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == 'GET':
        boletos = _get_boleto_queryset(request.user)
        status_filter = request.query_params.get('status')
        if status_filter:
            boletos = boletos.filter(status=status_filter)
        seller_filter = request.query_params.get('seller_uuid')
        if seller_filter and _is_gestor(request.user):
            boletos = boletos.filter(seller__uuid=seller_filter)
        boletos = boletos.order_by('-created_at')
        paginator = BoletoPagination()
        page = paginator.paginate_queryset(boletos, request)
        serializer = BoletoListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    if request.user.role == User.Role.FINANCEIRO:
        return Response(
            {'detail': 'Acesso negado.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    idempotency_key = request.headers.get('X-Idempotency-Key')
    if not idempotency_key:
        return Response(
            {'detail': 'Cabecalho X-Idempotency-Key obrigatorio.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = BoletoCreateSerializer(
        data=request.data, context={'request': request},
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    if request.user.role == User.Role.SELLER:
        seller = getattr(request.user, 'seller_profile', None)
        if not seller:
            return Response(
                {'detail': 'Perfil de vendedor nao encontrado.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        seller = data.pop('seller_uuid', None)
        if not seller:
            return Response(
                {'detail': 'seller_uuid obrigatorio para gestor.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    try:
        boleto = create_boleto(
            tenant=request.user.tenant,
            seller=seller,
            created_by=request.user,
            data=data,
            idempotency_key=idempotency_key,
        )
    except BoletoServiceError as e:
        return Response(
            {'detail': str(e), 'boleto_uuid': str(e.boleto.pk)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    log_action(
        request, 'receivable.boleto_created', boleto,
        {'amount_cents': boleto.amount_cents},
    )
    result = BoletoDetailSerializer(boleto).data
    return Response(result, status=status.HTTP_201_CREATED)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([BoletoCancelThrottle])
def boleto_detail_cancel(request, boleto_uuid):
    if not _tenant_enabled(request):
        return Response(
            {'detail': 'Funcionalidade nao disponivel.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    boleto = get_object_or_404(
        _get_boleto_queryset(request.user), uuid=boleto_uuid
    )

    if request.method == 'GET':
        serializer = BoletoDetailSerializer(boleto)
        return Response(serializer.data)

    if not _is_gestor(request.user):
        return Response(
            {'detail': 'Acesso negado.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if boleto.status not in (Boleto.Status.PENDENTE,):
        return Response(
            {'detail': 'Boleto nao pode ser cancelado no status atual.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = cancel_boleto(boleto)
    except BoletoServiceError as e:
        return Response(
            {'detail': str(e)},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    log_action(
        request, 'receivable.boleto_canceled', result,
        {'previous_status': boleto.status},
    )
    return Response(BoletoDetailSerializer(result).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def boleto_stats(request):
    if not _tenant_enabled(request):
        return Response(
            {'detail': 'Funcionalidade nao disponivel.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not _is_gestor(request.user):
        return Response(
            {'detail': 'Acesso negado.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    qs = Boleto.objects.filter(tenant=request.user.tenant)
    today = timezone.localdate()
    stats = {
        'total_a_receber': qs.filter(
            status=Boleto.Status.PENDENTE,
        ).count(),
        'vencendo_hoje': qs.filter(
            status=Boleto.Status.PENDENTE,
            due_date=today,
        ).count(),
        'vencidos': qs.filter(
            status=Boleto.Status.PENDENTE,
            due_date__lt=today,
        ).count(),
        'pagos_periodo': qs.filter(
            status=Boleto.Status.PAGO,
            paid_at__date=today,
        ).count(),
        'pagos_sem_alocacao': qs.filter(
            status=Boleto.Status.PAGO,
            allocations__isnull=True,
        ).count(),
        'falhas_emissao': qs.filter(
            status=Boleto.Status.FALHOU,
        ).count(),
        'cancelamentos_pendentes': qs.filter(
            status=Boleto.Status.CANCEL_PEND,
        ).count(),
    }
    return Response(stats)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def allocation_list_create(request):
    if not _tenant_enabled(request):
        return Response(
            {'detail': 'Funcionalidade nao disponivel.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not can_manage_receivable(request.user, request.user.tenant):
        return Response(
            {'detail': 'Acesso negado.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == 'GET':
        qs = ReceivableAllocation.objects.filter(
            tenant=request.user.tenant,
        ).select_related('boleto', 'boleto__seller')
        qs = qs.order_by('-allocated_at')
        paginator = AllocationPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            AllocationSerializer(page, many=True).data,
        )

    serializer = AllocationCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    boleto_uuid = serializer.validated_data['boleto_uuid']

    boleto = get_object_or_404(
        _get_boleto_queryset(request.user), uuid=boleto_uuid
    )

    try:
        allocation, created = allocate_boleto(
            boleto, allocated_by=request.user,
        )
    except AllocationDomainError as e:
        return Response(
            {'detail': str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    log_action(
        request, 'receivable.allocation_created', allocation,
        {'amount_cents': allocation.amount_cents},
    )
    return Response(
        AllocationSerializer(allocation).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def impact_review_list_approve(request):
    if not _tenant_enabled(request):
        return Response(
            {'detail': 'Funcionalidade nao disponivel.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not _is_gestor(request.user):
        return Response(
            {'detail': 'Acesso negado.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == 'GET':
        qs = CommissionImpactReview.objects.filter(
            tenant=request.user.tenant,
        ).select_related('seller').order_by('-created_at')
        paginator = ReviewPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            CommissionImpactReviewSerializer(page, many=True).data,
        )

    review_uuid = request.data.get('review_uuid')
    action = request.data.get('action')
    if not review_uuid or action not in ('approve', 'reject'):
        return Response(
            {'detail': 'review_uuid e action (approve/reject) sao obrigatorios.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    review = get_object_or_404(
        CommissionImpactReview,
        uuid=review_uuid,
        tenant=request.user.tenant,
    )

    if action == 'approve':
        try:
            from .commission_services import approve_and_apply_review
            approve_and_apply_review(review, request.user, '')
        except ValueError as e:
            return Response(
                {'detail': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        review.status = CommissionImpactReview.Status.REJECTED
        review.reviewed_by = request.user
        review.reviewed_at = timezone.now()
        review.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    return Response(
        CommissionImpactReviewSerializer(review).data,
    )
