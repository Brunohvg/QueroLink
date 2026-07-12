from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenBlacklistView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from . import views

router = DefaultRouter()
router.register(r'sellers', views.SellerViewSet, basename='api-seller')
router.register(r'sales', views.SaleViewSet, basename='api-sale')
router.register(r'commissions/periods', views.CommissionPeriodViewSet, basename='api-commission-period')
router.register(r'manager/day-justifications', views.SellerDayJustificationViewSet, basename='api-day-justification')

urlpatterns = [
    path('auth/login/', views.JWTLoginView.as_view(), name='api-login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='api-refresh'),
    path('auth/logout/', TokenBlacklistView.as_view(), name='api-logout'),

    path('seller/sales/', views.SellerSalesListView.as_view(), name='api-seller-sales'),
    path('seller/links/', views.SellerLinkCreateView.as_view(), name='seller-link-create'),
    path('seller/change-password/', views.ChangePasswordView.as_view(), name='api-change-password'),
    path('seller/statement/', views.SellerStatementView.as_view(), name='api-seller-statement-period'),
    path('seller/statement/<int:year>/<int:month>/', views.SellerStatementView.as_view(), name='api-seller-statement'),
    path('reports/monthly/<int:year>/<int:month>/', views.MonthlyReportView.as_view(), name='api-monthly-report'),
    path('reports/accounting/<int:year>/<int:month>/', views.AccountingExportView.as_view(), name='api-accounting-export'),
    path('reports/accounting/<int:year>/<int:month>/send/', views.AccountingEmailView.as_view(), name='api-accounting-send'),
    path('push/subscribe/', views.PushSubscribeView.as_view(), name='api-push-subscribe'),
    path('push/unsubscribe/', views.PushUnsubscribeView.as_view(), name='api-push-unsubscribe'),
    path('manager/sales/', views.ManagerSalesListView.as_view(), name='api-manager-sales'),
    path('manager/ranking/', views.RankingView.as_view(), name='api-ranking'),
    path('manager/ranking/annual/', views.AnnualRankingView.as_view(), name='api-ranking-annual'),
    path('manager/dashboard/summary/', views.DashboardSummaryView.as_view(), name='api-dashboard-summary'),
    path('manager/webhook-status/', views.WebhookStatusView.as_view(), name='api-webhook-status'),
    path('manager/seller/<uuid:seller_id>/', views.SellerDetailView.as_view(), name='api-seller-detail'),
    path('manager/seller/<uuid:seller_id>/csv/', views.SellerReportCsvView.as_view(), name='api-seller-report-csv'),
    path('manager/seller/<uuid:seller_id>/xlsx/', views.SellerReportExcelView.as_view(), name='api-seller-report-xlsx'),
    path('manager/seller/<uuid:seller_id>/pdf/', views.SellerReportPdfView.as_view(), name='api-seller-report-pdf'),
    path('manager/commissions/<str:status>/', views.CommissionPeriodsByStatusView.as_view(), name='api-commissions-by-status'),
    path('financial/payment-queue/', views.PaymentQueueView.as_view(), name='api-payment-queue'),
    path('financial/commissions/<uuid:pk>/csv/', views.CommissionPeriodCsvView.as_view(), name='api-commission-csv'),

    path('schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-swagger'),

    path('', include(router.urls)),
]
