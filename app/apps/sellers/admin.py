from django.contrib import admin
from .models import Seller


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'phone', 'commission_rate', 'is_active')
    search_fields = ('name', 'phone')
    list_filter = ('tenant', 'is_active')
    raw_id_fields = ('user',)
