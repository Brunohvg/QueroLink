from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('cadastro/', views.signup_view, name='signup'),
]
