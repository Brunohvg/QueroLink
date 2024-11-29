from django.db import models

class PagarMePayment(models.Model):
    transaction_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=50, default="pending")
    total_amount = models.IntegerField()
    max_installments = models.IntegerField()
    customer_name = models.CharField(max_length=255)
    whatsapp = models.CharField(max_length=20)
    link = models.URLField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Pagamento de {self.customer_name} - {self.transaction_id}"


"""from django.db import models

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
    #cliente_nome = models.CharField(max_length=100, blank=True, null=True)
    transaction_id = models.CharField(max_length=50)
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    link= models.CharField(max_length=400, blank=True, null=True)

    def __str__(self):
        return f"Transaction {self.id} - {self.transaction_id}"


        """
# Create your models here.
