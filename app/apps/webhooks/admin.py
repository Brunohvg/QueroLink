from django.contrib import admin
from .models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'gateway', 'processed', 'received_at')
    list_filter = ('gateway', 'processed')
    readonly_fields = ('received_at',)
