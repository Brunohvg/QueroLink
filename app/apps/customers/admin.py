from django.contrib import admin

from .models import Customer, CustomerActivity, CustomerIdentityConflict


admin.site.register(Customer)
admin.site.register(CustomerActivity)
admin.site.register(CustomerIdentityConflict)
