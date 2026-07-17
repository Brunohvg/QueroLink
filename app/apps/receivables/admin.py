from django.contrib import admin

from .models import Boleto


@admin.register(Boleto)
class BoletoAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'tenant', 'seller', 'amount_cents', 'status', 'due_date')
    list_filter = ('status', 'provider')
    search_fields = ('uuid', 'provider_order_id', 'provider_charge_id')
    readonly_fields = ('uuid', 'created_at', 'updated_at')
