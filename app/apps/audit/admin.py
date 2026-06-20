from django.contrib import admin
from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'model_name', 'object_id', 'user', 'ip_address', 'created_at')
    list_filter = ('model_name', 'created_at')
    search_fields = ('action', 'model_name', 'object_id', 'ip_address')
    readonly_fields = ('created_at',)
