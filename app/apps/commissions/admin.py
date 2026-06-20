from django.contrib import admin
from .models import CommissionPeriod, SellerCommission


class SellerCommissionInline(admin.TabularInline):
    model = SellerCommission
    extra = 0
    readonly_fields = ('total_sold_amount', 'commission_rate', 'commission_amount')


@admin.register(CommissionPeriod)
class CommissionPeriodAdmin(admin.ModelAdmin):
    list_display = ('month', 'year', 'tenant', 'status', 'created_at')
    list_filter = ('status', 'tenant', 'year')
    search_fields = ('tenant__company_name',)
    inlines = (SellerCommissionInline,)


@admin.register(SellerCommission)
class SellerCommissionAdmin(admin.ModelAdmin):
    list_display = ('seller', 'period', 'total_sold_amount', 'commission_rate', 'commission_amount', 'payment_date')
    list_filter = ('period__tenant', 'period__status')
    search_fields = ('seller__name',)
    raw_id_fields = ('seller', 'period')
