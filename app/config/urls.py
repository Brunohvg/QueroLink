from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.db import connections
from django.db.utils import OperationalError

def health_check(request):
    db_ok = True
    try:
        connections['default'].cursor()
    except OperationalError:
        db_ok = False

    return JsonResponse({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
    })

urlpatterns = [
    path('health/', health_check),
    path('admin/', admin.site.urls),
    path('dashboard/', include('app.apps.dashboard.urls')),
    path('', include('app.apps.orders.urls')),
    path('api/webhooks/', include('app.apps.webhooks.urls')),
    path('api/', include('app.apps.api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
