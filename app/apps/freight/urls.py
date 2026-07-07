from django.urls import path
from . import views

urlpatterns = [
    path('quote/', views.freight_quote_view, name='freight_quote'),
    path('test-cws/', views.freight_test_cws_view, name='freight_test_cws'),

]
