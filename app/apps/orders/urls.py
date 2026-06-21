from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('loja/<slug:tenant_slug>/', views.index, name='index'),
    path('loja/<slug:tenant_slug>/create_link/', views.create_link, name='create_link'),
    path('pago/<uuid:order_uuid>/', views.payment_success, name='payment_success'),
]
