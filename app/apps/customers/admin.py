from django.contrib import admin

from .models import Customer, CustomerActivity

admin.site.register(Customer)
admin.site.register(CustomerActivity)
