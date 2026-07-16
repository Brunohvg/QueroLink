from rest_framework.routers import DefaultRouter

from .api import BoletoViewSet

router = DefaultRouter()
router.register('', BoletoViewSet, basename='api-boleto')

urlpatterns = router.urls
