from django.core.exceptions import ObjectDoesNotExist
from rest_framework.permissions import BasePermission


class IsManagerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role in (
            request.user.Role.MANAGER,
            request.user.Role.ADMIN,
        )

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False
        tenant = getattr(obj, 'tenant', None)
        if tenant and tenant != request.user.tenant:
            return False
        return True


class IsFinancialOrAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role in (
            request.user.Role.FINANCEIRO,
            request.user.Role.ADMIN,
        )

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False
        tenant = getattr(obj, 'tenant', None)
        if tenant and tenant != request.user.tenant:
            return False
        return True


class IsSellerOwner(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role == request.user.Role.SELLER

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False
        obj_tenant = getattr(obj, 'tenant', None)
        if obj_tenant is not None and obj_tenant != request.user.tenant:
            return False
        try:
            seller_profile = request.user.seller_profile
        except ObjectDoesNotExist:
            return False
        if hasattr(obj, 'seller'):
            return obj.seller == seller_profile
        if hasattr(obj, 'seller_id'):
            return obj.seller_id == seller_profile.pk
        return False


class IsSameTenant(BasePermission):
    def has_object_permission(self, request, view, obj):
        tenant = getattr(obj, 'tenant', None)
        if tenant and tenant != request.user.tenant:
            return False
        return True
