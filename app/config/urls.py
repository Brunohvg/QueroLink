from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse

def health_check(request):
    return HttpResponse("OK")

urlpatterns = [
    path('health/', health_check),
    path('admin/', admin.site.urls),
    path('dashboard/', include('app.apps.dashboard.urls')),
    path('', include('app.apps.orders.urls')),
    path('api/webhooks/', include('app.apps.webhooks.urls')),
]
