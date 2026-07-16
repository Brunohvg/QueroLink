from django.contrib import admin

from .models import Boleto


@admin.register(Boleto)
class BoletoAdmin(admin.ModelAdmin):
    list_display = (
        'uuid', 'payer_name', 'seller', 'amount_cents',
        'due_date', 'status', 'created_at',
    )
    list_filter = ('status', 'gateway', 'due_date')
    search_fields = (
        'uuid', 'payer_name', 'payer_document',
        'gateway_order_id', 'gateway_charge_id',
    )
    readonly_fields = (
        'uuid', 'gateway_order_id', 'gateway_charge_id',
        'created_at', 'updated_at',
    )
