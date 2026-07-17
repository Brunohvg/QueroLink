from django.db.models import Q
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from app.apps.accounts.fields import compute_hash

from .models import Customer
from .permissions import IsCustomerManagerOrAdmin
from .serializers import CustomerSerializer


class CustomerPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class CustomerViewSet(ModelViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated, IsCustomerManagerOrAdmin]
    pagination_class = CustomerPagination
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = Customer.objects.filter(
            tenant=self.request.user.tenant
        ).prefetch_related('activities')
        name = str(self.request.query_params.get('name', '')).strip()
        document = ''.join(filter(
            str.isdigit, self.request.query_params.get('document', '')
        ))
        phone = ''.join(filter(
            str.isdigit, self.request.query_params.get('phone', '')
        ))
        activity = str(self.request.query_params.get('activity', '')).strip()
        if name:
            queryset = queryset.filter(name_hash=compute_hash(name))
        if document:
            queryset = queryset.filter(document_hash=compute_hash(document))
        if phone:
            queryset = queryset.filter(phone_hash=compute_hash(phone))
        if activity:
            queryset = queryset.filter(
                Q(activities__source__iexact=activity)
                | Q(activities__status__iexact=activity)
            ).distinct()
        return queryset
