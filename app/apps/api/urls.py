from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from . import views

router = DefaultRouter()
router.register(r'sellers', views.SellerViewSet, basename='api-seller')
router.register(r'sales', views.SaleViewSet, basename='api-sale')
router.register(r'commissions/periods', views.CommissionPeriodViewSet, basename='api-commission-period')

urlpatterns = [
    path('auth/login/', views.JWTLoginView.as_view(), name='api-login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='api-refresh'),

    path('seller/sales/', views.SellerSalesListView.as_view(), name='api-seller-sales'),
    path('manager/sales/', views.ManagerSalesListView.as_view(), name='api-manager-sales'),
    path('manager/ranking/', views.RankingView.as_view(), name='api-ranking'),
    path('manager/commissions/<str:status>/', views.CommissionPeriodsByStatusView.as_view(), name='api-commissions-by-status'),
    path('financial/commissions/<uuid:pk>/csv/', views.CommissionPeriodCsvView.as_view(), name='api-commission-csv'),

    path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-swagger'),

    path('', include(router.urls)),
]
