from django.contrib import admin
from .models import CommissionPeriod, SellerCommission, CommissionAdjustment


class SellerCommissionInline(admin.TabularInline):
    model = SellerCommission
    extra = 0
    readonly_fields = (
        'total_sold_amount', 'commission_rate', 'commission_amount',
    )
    fields = (
        'seller', 'total_sold_amount', 'commission_rate',
        'commission_amount', 'closed_at', 'paid_at', 'paid_amount',
    )


class CommissionAdjustmentInline(admin.TabularInline):
    model = CommissionAdjustment
    extra = 0
    readonly_fields = (
        'previous_amount', 'new_amount', 'difference',
        'adjusted_by', 'created_at',
    )
    fields = (
        'previous_amount', 'new_amount', 'difference',
        'reason', 'adjusted_by', 'created_at',
    )


@admin.register(CommissionPeriod)
class CommissionPeriodAdmin(admin.ModelAdmin):
    list_display = (
        'month', 'year', 'tenant', 'status',
        'closed_at', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'tenant', 'year')
    search_fields = ('tenant__company_name',)
    readonly_fields = (
        'closed_at', 'paid_at', 'adjusted_at', 'cancelled_at',
        'closed_by', 'paid_by', 'adjusted_by', 'cancelled_by',
    )
    fieldsets = (
        (None, {
            'fields': ('tenant', 'month', 'year', 'status'),
        }),
        ('Fechamento', {
            'fields': ('closed_at', 'closed_by'),
        }),
        ('Pagamento', {
            'fields': ('paid_at', 'paid_by'),
        }),
        ('Ajuste', {
            'fields': ('adjusted_at', 'adjusted_by', 'adjustment_reason'),
        }),
        ('Cancelamento', {
            'fields': ('cancelled_at', 'cancelled_by', 'cancel_reason'),
        }),
    )
    inlines = (SellerCommissionInline,)


@admin.register(SellerCommission)
class SellerCommissionAdmin(admin.ModelAdmin):
    list_display = (
        'seller', 'period', 'total_sold_amount', 'commission_rate',
        'commission_amount', 'closed_at', 'paid_at',
    )
    list_filter = ('period__tenant', 'period__status')
    search_fields = ('seller__name',)
    raw_id_fields = ('seller', 'period', 'closed_by', 'paid_by')
    readonly_fields = ('total_sold_amount', 'commission_amount')
    inlines = (CommissionAdjustmentInline,)


@admin.register(CommissionAdjustment)
class CommissionAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        'seller_commission', 'previous_amount', 'new_amount',
        'difference', 'adjusted_by', 'created_at',
    )
    list_filter = ('created_at',)
    search_fields = (
        'seller_commission__seller__name', 'reason',
    )
    readonly_fields = (
        'previous_amount', 'new_amount', 'difference',
        'adjusted_by', 'created_at',
    )
