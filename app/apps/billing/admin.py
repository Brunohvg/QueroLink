from django.contrib import admin
from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'plan', 'status', 'current_period_end', 'amount')
    list_filter = ('status', 'plan', 'billing_cycle')
    search_fields = ('tenant__company_name',)
