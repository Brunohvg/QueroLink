from django.urls import path, re_path
from . import views

app_name = 'webhooks'

urlpatterns = [
    path('pagarme/<slug:tenant_slug>/', views.pagarme_webhook, name='pagarme_webhook_tenant'),
    path('evolution/<str:instance_name>/<uuid:tenant_uuid>/<str:token>/', views.evolution_webhook, name='evolution_webhook'),
    re_path(r'^evolution/(?P<instance_name>[^/]+)/(?P<tenant_uuid>[^/]+)/$', views.evolution_webhook_legacy, name='evolution_webhook_legacy'),
]
