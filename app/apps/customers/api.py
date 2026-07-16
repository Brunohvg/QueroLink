from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.apps.accounts.models import User, tenant_has_feature
from app.apps.audit.models import AuditLog

from .models import Customer
from .serializers import CustomerConsentSerializer, CustomerSerializer


class CustomerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "uuid"
    lookup_url_kwarg = "uuid"

    def get_queryset(self):
        user = self.request.user
        if user.role not in (User.Role.ADMIN, User.Role.MANAGER) or not user.tenant_id:
            raise PermissionDenied("Acesso exclusivo do gestor.")
        if not tenant_has_feature(user.tenant, "customer_management"):
            raise PermissionDenied(
                "Gestao de clientes disponivel nos planos Pro e Business."
            )
        return Customer.objects.filter(tenant=user.tenant).prefetch_related(
            "activities"
        )

    @extend_schema(request=CustomerConsentSerializer, responses=CustomerSerializer)
    @action(detail=True, methods=["post"], url_path="consent")
    def consent(self, request, uuid=None):
        customer = self.get_object()
        serializer = CustomerConsentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer.marketing_consent = serializer.validated_data["marketing_consent"]
        customer.marketing_consent_at = timezone.now()
        customer.marketing_consent_source = serializer.validated_data.get(
            "consent_source", ""
        )
        customer.marketing_consent_updated_by = request.user
        customer.notes = serializer.validated_data.get("notes", customer.notes)
        customer.save(
            update_fields=[
                "marketing_consent",
                "marketing_consent_at",
                "marketing_consent_source",
                "marketing_consent_updated_by",
                "notes",
                "updated_at",
            ]
        )
        AuditLog.objects.create(
            user=request.user,
            tenant=request.user.tenant,
            action="customer.consent_updated",
            model_name="Customer",
            object_id=str(customer.uuid),
            changes={"marketing_consent": customer.marketing_consent},
        )
        return Response(CustomerSerializer(customer).data, status=status.HTTP_200_OK)
