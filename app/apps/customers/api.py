from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from app.apps.accounts.models import User, tenant_has_feature

from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "uuid"
    lookup_url_kwarg = "uuid"

    def get_queryset(self):
        user = self.request.user
        if user.role not in (User.Role.ADMIN, User.Role.MANAGER) or not user.tenant_id:
            raise PermissionDenied("Acesso exclusivo do gestor.")
        if not tenant_has_feature(user.tenant, "boletos"):
            raise PermissionDenied(
                "Gestao de clientes disponivel nos planos Pro e Business."
            )
        return Customer.objects.filter(tenant=user.tenant).prefetch_related(
            "activities"
        )
