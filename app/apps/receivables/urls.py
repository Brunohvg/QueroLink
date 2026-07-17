from django.urls import path

from . import document_views, api


app_name = 'receivables'

urlpatterns = [
    path(
        'boletos/<uuid:boleto_uuid>/documents/<str:document_type>/upload/',
        document_views.upload_invoice,
        name='invoice-upload',
    ),
    path(
        'boletos/<uuid:boleto_uuid>/documents/<str:document_type>/download/',
        document_views.download_invoice,
        name='invoice-download',
    ),
    path('boletos/', api.boleto_list_create, name='boleto-list-create'),
    path(
        'boletos/<uuid:boleto_uuid>/',
        api.boleto_detail_cancel,
        name='boleto-detail-cancel',
    ),
    path('boletos/stats/', api.boleto_stats, name='boleto-stats'),
    path(
        'allocations/', api.allocation_list_create,
        name='allocation-list-create',
    ),
    path(
        'impact-reviews/', api.impact_review_list_approve,
        name='impact-review-list-approve',
    ),
]
