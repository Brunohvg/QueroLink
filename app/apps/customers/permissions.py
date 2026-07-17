from rest_framework.permissions import BasePermission


class IsCustomerManagerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user.is_authenticated
            and user.tenant_id
            and user.role in (user.Role.ADMIN, user.Role.MANAGER)
        )

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view) and obj.tenant_id == request.user.tenant_id
