from django.urls import path
from . import views

app_name = 'webhooks'

urlpatterns = [
    path('pagarme/', views.pagarme_webhook, name='pagarme_webhook'),
]
