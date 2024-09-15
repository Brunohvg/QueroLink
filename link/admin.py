from django.contrib import admin
from .models import PagarMeOrder, PagarMeTransaction
# Register your models here.

admin.site.register(PagarMeTransaction)
admin.site.register(PagarMeOrder)