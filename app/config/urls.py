from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.http import JsonResponse
from django.db import connections
from django.db.utils import OperationalError
from django.views.generic import RedirectView
from django_ratelimit.decorators import ratelimit
from app.apps.accounts import views as account_views
from app.apps.dashboard import desktop_views


@method_decorator(ratelimit(key='ip', rate='3/h', method='POST', block=True), name='post')
@method_decorator(csrf_protect, name='post')
class RateLimitedPasswordResetView(auth_views.PasswordResetView):
    pass

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
    path('', account_views.landing_page, name='root'),
    path('health/', health_check),
    path('admin/', admin.site.urls),
    path('admin/metrics/', desktop_views.admin_metrics, name='admin_metrics'),
    path('dashboard/', include('app.apps.dashboard.urls')),
    path('', include('app.apps.accounts.urls')),
    path('', include('app.apps.orders.urls')),
    path('api/webhooks/', include('app.apps.webhooks.urls')),
    path('api/billing/', include('app.apps.billing.urls')),
    path('api/freight/', include('app.apps.freight.urls')),
    path('api/', include('app.apps.api.urls')),

    path('dashboard/esqueci-senha/', RateLimitedPasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.txt',
        html_email_template_name='registration/password_reset_email.html',
        subject_template_name='registration/password_reset_subject.txt',
        success_url=reverse_lazy('password_reset_done'),
    ), name='password_reset'),
    path('dashboard/esqueci-senha/enviado/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('dashboard/redefinir-senha/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
        success_url=reverse_lazy('password_reset_complete'),
    ), name='password_reset_confirm'),
    path('dashboard/redefinir-senha/concluido/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
