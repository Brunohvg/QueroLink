from django.contrib import admin
from .models import Sale


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'tenant', 'seller', 'origin', 'amount', 'sale_date', 'created_at')
    list_filter = ('origin', 'tenant', 'sale_date')
    search_fields = ('uuid',)
    raw_id_fields = ('seller', 'order', 'created_by')
