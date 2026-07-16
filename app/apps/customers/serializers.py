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
    consent_label = serializers.CharField(
        source="get_marketing_consent_display", read_only=True
    )
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
            "notes",
            "marketing_consent",
            "consent_label",
            "marketing_consent_at",
            "marketing_consent_source",
            "activities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CustomerConsentSerializer(serializers.Serializer):
    marketing_consent = serializers.ChoiceField(choices=Customer.ConsentStatus.choices)
    consent_source = serializers.CharField(
        max_length=100, allow_blank=True, required=False
    )
    notes = serializers.CharField(allow_blank=True, required=False)

    def validate(self, attrs):
        if (
            attrs["marketing_consent"]
            in (
                Customer.ConsentStatus.GRANTED,
                Customer.ConsentStatus.REVOKED,
            )
            and not attrs.get("consent_source", "").strip()
        ):
            raise serializers.ValidationError(
                {
                    "consent_source": (
                        "Informe a origem do consentimento ou da revogacao."
                    ),
                }
            )
        return attrs
