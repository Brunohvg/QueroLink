from django.urls import path
from . import views, mobile_views

app_name = 'dashboard'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_home, name='home'),
    path('sellers/create/', views.seller_create, name='seller_create'),
]

urlpatterns += [
    path('mobile/login/', mobile_views.mobile_login, name='mobile_login'),
    path('mobile/logout/', mobile_views.mobile_logout, name='mobile_logout'),
    path('mobile/forgot-password/', mobile_views.mobile_forgot_password, name='mobile_forgot_password'),
    path('mobile/', mobile_views.mobile_home, name='mobile_home'),
    path('mobile/lancar/', mobile_views.mobile_lancar_venda, name='mobile_lancar_venda'),
    path('mobile/vendas/', mobile_views.mobile_minhas_vendas, name='mobile_minhas_vendas'),
    path('mobile/desempenho/', mobile_views.mobile_meu_desempenho, name='mobile_meu_desempenho'),
]
