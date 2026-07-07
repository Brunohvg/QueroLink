from django.contrib import admin
from .models import Order, PaymentLink


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'tenant', 'seller', 'customer_name', 'total_amount_decimal', 'status', 'created_at')
    list_filter = ('status', 'tenant')
    search_fields = ('uuid', 'customer_name')
    raw_id_fields = ('seller',)


@admin.register(PaymentLink)
class PaymentLinkAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'order', 'short_code', 'clicks_count', 'expires_at', 'created_at')
    search_fields = ('short_code', 'gateway_link_id')
    raw_id_fields = ('order',)
