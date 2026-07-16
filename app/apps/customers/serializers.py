from rest_framework import serializers

from .models import Customer, CustomerActivity


class CustomerActivitySerializer(serializers.ModelSerializer):
    source_label = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = CustomerActivity
        fields = [
            "source",
            "source_label",
            "source_uuid",
            "seller_name",
            "amount_cents",
            "status",
            "occurred_at",
        ]


class CustomerSerializer(serializers.ModelSerializer):
    activities = CustomerActivitySerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "uuid",
            "name",
            "email",
            "phone",
            "document",
            "document_type",
            "zip_code",
            "street",
            "number",
            "complement",
            "neighborhood",
            "city",
            "state",
            "activities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
