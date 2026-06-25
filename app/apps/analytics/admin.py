from django.contrib import admin
from .models import LinkClick


@admin.register(LinkClick)
class LinkClickAdmin(admin.ModelAdmin):
    list_display = ('payment_link', 'ip_hash', 'clicked_at')
    list_filter = ('clicked_at',)
    raw_id_fields = ('payment_link',)
