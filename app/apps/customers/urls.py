from rest_framework.routers import DefaultRouter

from .api import CustomerViewSet


router = DefaultRouter()
router.register('', CustomerViewSet, basename='customer')

urlpatterns = router.urls
