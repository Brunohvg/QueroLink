from django.urls import path

from . import document_views


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
]
