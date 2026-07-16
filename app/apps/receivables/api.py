from datetime import date, timedelta

from django.db.models import Q, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from app.apps.accounts.models import User, tenant_has_feature
from app.apps.audit.models import AuditLog
from app.apps.sellers.models import Seller

from .models import Boleto
from .serializers import BoletoSerializer
from .services import BoletoServiceError, cancel_boleto, save_invoice_files
from .throttles import BoletoCreateThrottle, BoletoResendThrottle


class BoletoPagination(PageNumberPagination):
    page_size = 50
    max_page_size = 100


@extend_schema_view(
    list=extend_schema(description='Lista boletos no escopo do usuario.'),
    create=extend_schema(description='Emite um novo boleto.'),
    retrieve=extend_schema(description='Detalha um boleto no escopo do usuario.'),
)
class BoletoViewSet(viewsets.ModelViewSet):
    queryset = Boleto.objects.all()
    serializer_class = BoletoSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']
    pagination_class = BoletoPagination
    lookup_field = 'uuid'
    lookup_url_kwarg = 'uuid'

    def get_throttles(self):
        if self.action == 'create':
            return [BoletoCreateThrottle()]
        if self.action == 'resend_email':
            return [BoletoResendThrottle()]
        return [UserRateThrottle()]

    def _ensure_access(self):
        user = self.request.user
        if not user.tenant_id or user.role not in (
            User.Role.SELLER, User.Role.ADMIN, User.Role.MANAGER,
        ):
            raise PermissionDenied('Acesso nao permitido.')
        if not tenant_has_feature(user.tenant, 'boletos'):
            raise PermissionDenied('Boletos disponiveis nos planos Pro e Business.')

    def get_queryset(self):
        self._ensure_access()
        user = self.request.user
        queryset = Boleto.objects.filter(tenant=user.tenant).select_related(
            'seller', 'created_by', 'launched_sale',
        )
        if user.role == User.Role.SELLER:
            try:
                queryset = queryset.filter(seller=user.seller_profile)
            except Seller.DoesNotExist:
                return queryset.none()
        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value.upper())
        seller_uuid = self.request.query_params.get('seller')
        if seller_uuid and user.role in (User.Role.ADMIN, User.Role.MANAGER):
            queryset = queryset.filter(seller_id=seller_uuid)
        due_before = self._parse_date('due_before')
        due_after = self._parse_date('due_after')
        if due_before:
            queryset = queryset.filter(due_date__lte=due_before)
        if due_after:
            queryset = queryset.filter(due_date__gte=due_after)
        search = self.request.query_params.get('search', '').strip()
        if search:
            digits = ''.join(filter(str.isdigit, search))
            query = Q(payer_name__icontains=search)
            if digits:
                query |= Q(payer_document__icontains=digits)
            queryset = queryset.filter(query)
        return queryset

    def _parse_date(self, key):
        raw = self.request.query_params.get(key)
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError({key: 'Data invalida. Use AAAA-MM-DD.'}) from exc

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, uuid=None):
        boleto = self.get_object()
        try:
            boleto = cancel_boleto(boleto, request.user)
        except BoletoServiceError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(self.get_serializer(boleto).data)

    @action(detail=True, methods=['post'], url_path='resend-email')
    def resend_email(self, request, uuid=None):
        boleto = self.get_object()
        if not boleto.payer_email:
            raise ValidationError({'detail': 'Comprador sem e-mail cadastrado.'})
        from .tasks import send_boleto_email
        send_boleto_email.delay(str(boleto.uuid), 'created')
        AuditLog.objects.create(
            user=request.user,
            tenant=boleto.tenant,
            action='boleto.email_resent',
            model_name='Boleto',
            object_id=str(boleto.uuid),
            changes={},
        )
        return Response({'detail': 'E-mail enfileirado.'}, status=status.HTTP_202_ACCEPTED)

    @extend_schema(description='Anexa ou substitui PDF/XML da nota fiscal.')
    @action(detail=True, methods=['post'], url_path='invoice')
    def invoice(self, request, uuid=None):
        boleto = self.get_object()
        try:
            boleto = save_invoice_files(
                boleto,
                request.user,
                pdf=request.FILES.get('invoice_pdf'),
                xml=request.FILES.get('invoice_xml'),
            )
        except BoletoServiceError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        except Exception as exc:
            raise ValidationError(
                {'detail': 'Erro ao processar o upload. Tente novamente.'},
            ) from exc
        if request.data.get('send_email') and boleto.payer_email:
            from .tasks import send_boleto_email
            send_boleto_email.delay(str(boleto.uuid), 'invoice', True)
        return Response(self.get_serializer(boleto).data)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        self._ensure_access()
        if request.user.role not in (User.Role.ADMIN, User.Role.MANAGER):
            raise PermissionDenied('Resumo disponivel somente para gestores.')
        today = timezone.localdate()
        queryset = Boleto.objects.filter(tenant=request.user.tenant)

        def total(qs):
            return qs.aggregate(value=Sum('amount_cents'))['value'] or 0

        return Response({
            'a_receber_cents': total(queryset.filter(status=Boleto.Status.PENDENTE)),
            'vencendo_7d_cents': total(queryset.filter(
                status=Boleto.Status.PENDENTE,
                due_date__gte=today,
                due_date__lte=today + timedelta(days=7),
            )),
            'vencidos_cents': total(queryset.filter(status=Boleto.Status.VENCIDO)),
            'pagos_mes_cents': queryset.filter(
                status=Boleto.Status.PAGO,
                paid_at__year=today.year,
                paid_at__month=today.month,
            ).aggregate(value=Sum('paid_amount_cents'))['value'] or 0,
            'aguardando_lancamento': queryset.filter(
                status=Boleto.Status.PAGO,
                launched_sale__isnull=True,
            ).count(),
        })
