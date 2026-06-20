from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'order', 'gateway_name', 'payment_method', 'installments', 'status', 'created_at')
    list_filter = ('status', 'gateway_name', 'payment_method')
    search_fields = ('uuid', 'gateway_transaction_id', 'gateway_order_id')
    raw_id_fields = ('order',)
    readonly_fields = ('raw_callback_payload',)
