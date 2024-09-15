from django.urls import path
from . import views

app_name = 'link'

urlpatterns = [
    path('', views.index, name='index'),
    path('create_link/', views.create_link, name='create_link'),
    path('sucess/<str:transaction_id>/', views.paid_transaction, name='paid_transaction')
]
