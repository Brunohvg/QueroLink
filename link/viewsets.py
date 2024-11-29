from rest_framework import viewsets
from .models import PagarMePayment
from .serializers import PagarMePaymentSerializer
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

class PagarMePaymentViewSet(viewsets.ModelViewSet):
    queryset = PagarMePayment.objects.all()
    serializer_class = PagarMePaymentSerializer
    permission_classes = [IsAuthenticated]  # Adiciona a exigência de autenticação

    def get_queryset(self):
        queryset = PagarMePayment.objects.all()
        transaction_id = self.request.query_params.get('transaction_id', None)

        if transaction_id:
            # Filtra o queryset diretamente
            queryset = queryset.filter(transaction_id=transaction_id)
            if not queryset:  # O queryset já é vazio, portanto não precisa do exists()
                raise NotFound(detail=f"Pagamento com transaction_id {transaction_id} não encontrado.", code=status.HTTP_404_NOT_FOUND)

        return queryset
