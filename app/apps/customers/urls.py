from rest_framework.routers import DefaultRouter

from .api import CustomerViewSet

router = DefaultRouter()
router.register("", CustomerViewSet, basename="api-customer")

urlpatterns = router.urls
