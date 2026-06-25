from django.urls import path
from . import views

app_name = 'webhooks'

urlpatterns = [
    path('pagarme/', views.pagarme_webhook, name='pagarme_webhook'),
    path('evolution/<str:instance_name>/<uuid:tenant_uuid>/<str:token>/', views.evolution_webhook, name='evolution_webhook'),
]
