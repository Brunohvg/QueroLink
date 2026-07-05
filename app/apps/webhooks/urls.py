from django.urls import path
from . import views

app_name = 'webhooks'

urlpatterns = [
    path('pagarme/<slug:tenant_slug>/', views.pagarme_webhook, name='pagarme_webhook_tenant'),
    path('billing/', views.billing_webhook, name='billing_webhook'),
    path('evolution/<str:instance_name>/<uuid:tenant_uuid>/<str:token>/', views.evolution_webhook, name='evolution_webhook'),
]
