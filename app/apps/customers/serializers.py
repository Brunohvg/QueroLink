from django.db import transaction
from rest_framework import serializers

from app.apps.audit.models import AuditLog

from .models import Customer, CustomerActivity


def _mask_document(value):
    digits = ''.join(filter(str.isdigit, value or ''))
    if len(digits) == 11:
        return f'***.***.***-{digits[-2:]}'
    if len(digits) == 14:
        return f'**.***.***/****-{digits[-2:]}'
    return f'***{digits[-2:]}' if digits else ''


def _mask_phone(value):
    digits = ''.join(filter(str.isdigit, value or ''))
    if len(digits) >= 10:
        return f'({digits[:2]}) *****-{digits[-4:]}'
    return f'***{digits[-2:]}' if digits else ''


def _mask_email(value):
    value = (value or '').strip()
    if '@' not in value:
        return ''
    local, domain = value.rsplit('@', 1)
    return f'{local[:1]}***@{domain}'


class CustomerActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerActivity
        fields = [
            'uuid', 'source', 'source_uuid', 'seller_name', 'amount_cents',
            'status', 'occurred_at',
        ]


class CustomerSerializer(serializers.ModelSerializer):
    document_masked = serializers.SerializerMethodField()
    phone_masked = serializers.SerializerMethodField()
    email_masked = serializers.SerializerMethodField()
    latest_activity = serializers.SerializerMethodField()
    consent_metadata = serializers.SerializerMethodField()
    operational_consent_source = serializers.CharField(
        max_length=100, write_only=True, required=False
    )
    marketing_consent_source = serializers.CharField(
        max_length=100, write_only=True, required=False
    )

    class Meta:
        model = Customer
        fields = [
            'uuid', 'name', 'document_type', 'document_masked', 'phone_masked',
            'email_masked', 'operational_consent', 'marketing_consent',
            'operational_consent_source', 'marketing_consent_source',
            'consent_metadata', 'latest_activity', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'uuid', 'name', 'document_type', 'document_masked', 'phone_masked',
            'email_masked', 'consent_metadata', 'latest_activity', 'created_at',
            'updated_at',
        ]

    def get_document_masked(self, obj):
        return _mask_document(obj.document)

    def get_phone_masked(self, obj):
        return _mask_phone(obj.phone)

    def get_email_masked(self, obj):
        return _mask_email(obj.email)

    def get_latest_activity(self, obj):
        activity = obj.activities.order_by('-occurred_at').first()
        return CustomerActivitySerializer(activity).data if activity else None

    def get_consent_metadata(self, obj):
        logs = AuditLog.objects.filter(
            tenant=obj.tenant,
            model_name='Customer',
            object_id=str(obj.uuid),
            action='customer.consent_updated',
        ).select_related('user').order_by('-created_at')
        metadata = {'operational': None, 'marketing': None}
        for log in logs:
            consent_type = (log.changes or {}).get('consent_type')
            if consent_type in metadata and metadata[consent_type] is None:
                metadata[consent_type] = {
                    'updated_at': log.created_at,
                    'source': (log.changes or {}).get('source', ''),
                    'updated_by': log.user.username if log.user else None,
                }
            if all(value is not None for value in metadata.values()):
                break
        return metadata

    def validate(self, attrs):
        for consent_type in ('operational', 'marketing'):
            consent_field = f'{consent_type}_consent'
            source_field = f'{consent_type}_consent_source'
            if consent_field in attrs and not str(attrs.get(source_field, '')).strip():
                raise serializers.ValidationError({
                    source_field: 'Informe a origem da atualizacao do consentimento.'
                })
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        request = self.context['request']
        updates = []
        for consent_type in ('operational', 'marketing'):
            consent_field = f'{consent_type}_consent'
            source_field = f'{consent_type}_consent_source'
            source = str(validated_data.pop(source_field, '')).strip()
            if consent_field not in validated_data:
                continue
            old_value = getattr(instance, consent_field)
            new_value = validated_data[consent_field]
            setattr(instance, consent_field, new_value)
            updates.append((consent_type, old_value, new_value, source))
        if updates:
            instance.save(update_fields=[
                f'{consent_type}_consent' for consent_type, *_ in updates
            ] + ['updated_at'])
            for consent_type, old_value, new_value, source in updates:
                AuditLog.objects.create(
                    user=request.user,
                    tenant=request.user.tenant,
                    action='customer.consent_updated',
                    model_name='Customer',
                    object_id=str(instance.uuid),
                    changes={
                        'consent_type': consent_type,
                        'old': old_value,
                        'new': new_value,
                        'source': source,
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
        return instance
