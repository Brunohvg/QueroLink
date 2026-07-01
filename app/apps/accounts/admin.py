from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Tenant, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'cnpj', 'is_active', 'created_at')
    search_fields = ('company_name', 'cnpj')
    list_filter = ('is_active',)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'tenant', 'is_active')
    list_filter = ('role', 'tenant', 'is_active')
    search_fields = ('username', 'email')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Comissã', {'fields': ('role', 'tenant')}),
    )
