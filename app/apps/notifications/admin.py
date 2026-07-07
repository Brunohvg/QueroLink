from django.contrib import admin
from .models import MessageTemplate, Notification


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'event_type', 'channel', 'is_active')
    list_filter = ('tenant', 'event_type', 'channel', 'is_active')
    search_fields = ('tenant__company_name', 'body')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'tenant', 'event_type', 'channel', 'recipient', 'status', 'retry_count', 'created_at')
    list_filter = ('status', 'event_type', 'channel', 'tenant')
    search_fields = ('uuid',)
    readonly_fields = ('created_at', 'updated_at')
