from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('', views.index, name='index'),
    path('create_link/', views.create_link, name='create_link'),
    path('pago/<uuid:order_uuid>/', views.payment_success, name='payment_success'),
]
