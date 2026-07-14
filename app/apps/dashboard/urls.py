from django.urls import path
from . import views, mobile_views, desktop_views

app_name = 'dashboard'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_home, name='home'),
    path('plano-expirado/', views.plano_expirado, name='plano_expirado'),
    path('assinatura/', views.assinatura, name='assinatura'),

    path('gestor/', desktop_views.gestor_home, name='gestor_home'),
    path('gestor/ranking/', desktop_views.gestor_ranking, name='gestor_ranking'),
    path('gestor/vendedores/', desktop_views.gestor_vendedores, name='gestor_vendedores'),
    path('gestor/importar-vendas/', desktop_views.gestor_importar_vendas, name='gestor_importar_vendas'),
    path('gestor/vendedores/<uuid:seller_id>/', desktop_views.gestor_vendedor_detalhe, name='gestor_vendedor_detalhe'),
    path('gestor/fechamento/', desktop_views.gestor_fechamento, name='gestor_fechamento'),
    path('gestor/contabilidade/', desktop_views.gestor_contabilidade, name='gestor_contabilidade'),
    path('gestor/previa-fechamento/', desktop_views.gestor_previa_fechamento, name='gestor_previa_fechamento'),
    path('gestor/configuracoes/', desktop_views.gestor_configuracoes, name='gestor_configuracoes'),
    path('gestor/configuracoes/whatsapp/qrcode/', desktop_views.whatsapp_instance_status, name='whatsapp_qrcode'),
    path('gestor/configuracoes/whatsapp/status/', desktop_views.whatsapp_connection_state, name='whatsapp_status'),
    path('gestor/configuracoes/whatsapp/disconnect/', desktop_views.whatsapp_disconnect, name='whatsapp_disconnect'),
    path('gestor/configuracoes/whatsapp/delete/', desktop_views.whatsapp_delete_instance, name='whatsapp_delete_instance'),
    path('gestor/webhooks/', desktop_views.gestor_webhooks, name='gestor_webhooks'),
    path('gestor/links/', desktop_views.gestor_links, name='gestor_links'),
    path('gestor/links/<uuid:order_uuid>/', desktop_views.gestor_link_detalhe, name='gestor_link_detalhe'),
    path('gestor/links/<uuid:order_uuid>/cancelar/', desktop_views.gestor_link_cancelar, name='gestor_link_cancelar'),
    path('gestor/links/<uuid:order_uuid>/verificar-pagamento/', desktop_views.gestor_link_verificar_pagamento, name='gestor_link_verificar_pagamento'),
    path('gestor/links/<uuid:order_uuid>/reenviar-vendedor/', desktop_views.gestor_link_reenviar_vendedor, name='gestor_link_reenviar_vendedor'),
    path('gestor/links/<uuid:order_uuid>/estornar/', desktop_views.gestor_link_estornar, name='gestor_link_estornar'),

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
    path('mobile/links/', mobile_views.mobile_links, name='mobile_links'),
    path('mobile/ranking/', mobile_views.mobile_ranking, name='mobile_ranking'),
    path('mobile/perfil/', mobile_views.mobile_perfil, name='mobile_perfil'),
    path('mobile/frete/', mobile_views.mobile_frete, name='mobile_frete'),
]
