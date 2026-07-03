from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from django.db import connections
from django.db.utils import OperationalError
from django.views.generic import RedirectView

def health_check(request):
    db_ok = True
    try:
        connections['default'].cursor()
    except OperationalError:
        db_ok = False

    payload = {
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
    }
    status_code = 200 if db_ok else 503
    return JsonResponse(payload, status=status_code)

urlpatterns = [
    path('', RedirectView.as_view(url='/dashboard/login/', permanent=False), name='root'),
    path('health/', health_check),
    path('admin/', admin.site.urls),
    path('dashboard/', include('app.apps.dashboard.urls')),
    path('', include('app.apps.accounts.urls')),
    path('', include('app.apps.orders.urls')),
    path('api/webhooks/', include('app.apps.webhooks.urls')),
    path('api/billing/', include('app.apps.billing.urls')),
    path('api/', include('app.apps.api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
