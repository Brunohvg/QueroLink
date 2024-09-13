from django.db import models

class PagarMeOrder(models.Model):
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    max_installments = models.IntegerField()
    customer_name = models.CharField(max_length=100)
    whatsapp = models.CharField(max_length=15)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order {self.id} - {self.customer_name}"

class PagarMeTransaction(models.Model):
    order = models.ForeignKey(PagarMeOrder, on_delete=models.CASCADE)
    transaction_id = models.CharField(max_length=50)
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transaction {self.id} - {self.transaction_id}"


        
# Create your models here.
