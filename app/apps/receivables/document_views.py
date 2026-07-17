import logging

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from app.apps.accounts.models import User

from .document_services import (
    InvalidInvoiceDocument,
    InvoiceStorageError,
    upload_invoice_document,
)
from .models import Boleto


logger = logging.getLogger(__name__)


def _has_document_permission(user):
    return user.role in (User.Role.ADMIN, User.Role.MANAGER)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_invoice(request, boleto_uuid, document_type):
    if not _has_document_permission(request.user):
        return Response({'detail': 'Acesso negado.'}, status=status.HTTP_403_FORBIDDEN)
    boleto = get_object_or_404(
        Boleto, pk=boleto_uuid, tenant=request.user.tenant
    )
    uploaded_file = request.FILES.get('file')
    if uploaded_file is None:
        return Response(
            {'detail': 'Arquivo obrigatorio.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        upload_invoice_document(
            boleto=boleto,
            document_type=document_type,
            uploaded_file=uploaded_file,
            user=request.user,
        )
    except InvalidInvoiceDocument as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except InvoiceStorageError:
        logger.exception(
            'Falha no upload fiscal boleto=%s tipo=%s', boleto.uuid, document_type
        )
        return Response(
            {'detail': 'Nao foi possivel armazenar o documento.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({'document_type': document_type}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def download_invoice(request, boleto_uuid, document_type):
    if not _has_document_permission(request.user):
        return Response({'detail': 'Acesso negado.'}, status=status.HTTP_403_FORBIDDEN)
    boleto = get_object_or_404(
        Boleto, pk=boleto_uuid, tenant=request.user.tenant
    )
    if document_type not in ('pdf', 'xml'):
        return Response(
            {'detail': 'Tipo de documento invalido.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    field_file = getattr(boleto, f'invoice_{document_type}')
    if not field_file:
        return Response(
            {'detail': 'Documento nao encontrado.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    try:
        response = FileResponse(
            field_file.open('rb'),
            as_attachment=True,
            filename=f'boleto-{boleto.uuid}-invoice.{document_type}',
            content_type=(
                'application/pdf' if document_type == 'pdf' else 'application/xml'
            ),
        )
    except Exception:
        logger.exception(
            'Falha no download fiscal boleto=%s tipo=%s', boleto.uuid, document_type
        )
        return Response(
            {'detail': 'Nao foi possivel carregar o documento.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return response
