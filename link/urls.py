from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .viewsets import PagarMePaymentViewSet  # Importando o viewset de viewset.py
from . import views

app_name = 'link'

router = DefaultRouter()
router.register(r'pagamentos', PagarMePaymentViewSet)

urlpatterns = [
    path('', views.index, name='index'),
    path('create_link/', views.create_link, name='create_link'),
    path('api/v1/', include(router.urls)),  # URL da API que inclui todas as rotas registradas pelo DefaultRouter
]
