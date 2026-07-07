from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('plans/', views.PlanListView.as_view(), name='api-billing-plans'),
    path('subscription/upgrade/', views.UpgradeSubscriptionView.as_view(), name='api-billing-upgrade'),
    path('subscription/cancel/', views.CancelSubscriptionView.as_view(), name='api-billing-cancel'),
]
