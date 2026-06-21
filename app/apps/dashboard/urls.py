from django.urls import path
from . import views, mobile_views, desktop_views

app_name = 'dashboard'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_home, name='home'),
    path('sellers/create/', views.seller_create, name='seller_create'),

    path('gestor/', desktop_views.gestor_home, name='gestor_home'),
    path('gestor/ranking/', desktop_views.gestor_ranking, name='gestor_ranking'),
    path('gestor/vendedores/', desktop_views.gestor_vendedores, name='gestor_vendedores'),
    path('gestor/fechamento/', desktop_views.gestor_fechamento, name='gestor_fechamento'),
    path('gestor/configuracoes/', desktop_views.gestor_configuracoes, name='gestor_configuracoes'),

    path('financeiro/fila/', desktop_views.financeiro_fila, name='financeiro_fila'),
    path('financeiro/historico/', desktop_views.financeiro_historico, name='financeiro_historico'),
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
