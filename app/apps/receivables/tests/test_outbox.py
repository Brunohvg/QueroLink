from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from app.apps.accounts.models import Tenant
from app.apps.receivables.models import IntegrationOutbox
from app.apps.receivables.services import (
    claim_outbox_events,
    complete_outbox_event,
    fail_outbox_event,
)
from app.apps.receivables.tasks import OUTBOX_HANDLERS, process_outbox_event


class OutboxTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Outbox Tenant')
        self.event = IntegrationOutbox.objects.create(
            tenant=self.tenant,
            aggregate_type='boleto',
            aggregate_uuid='12345678-1234-5678-1234-567812345678',
            event_type='boleto.test',
            event_key='boleto.test:12345678-1234-5678-1234-567812345678',
            payload={'boleto_uuid': '12345678-1234-5678-1234-567812345678'},
        )
        OUTBOX_HANDLERS.clear()

    def tearDown(self):
        OUTBOX_HANDLERS.clear()

    def test_claim_retry_and_completion(self):
        claimed = claim_outbox_events(1)
        self.assertEqual([event.pk for event in claimed], [self.event.pk])
        self.assertEqual(claimed[0].attempt_count, 1)

        failed = fail_outbox_event(self.event.pk, 'error\nsecret', retry_delay_seconds=0)
        self.assertEqual(failed.status, IntegrationOutbox.Status.FAILED)
        self.assertEqual(failed.last_error, 'error secret')

        retried = claim_outbox_events(1)
        self.assertEqual(retried[0].attempt_count, 2)
        completed = complete_outbox_event(self.event.pk)
        self.assertEqual(completed.status, IntegrationOutbox.Status.PROCESSED)
        self.assertIsNotNone(completed.processed_at)

    def test_consumer_error_does_not_change_financial_data(self):
        OUTBOX_HANDLERS['boleto.test'] = lambda event: (_ for _ in ()).throw(
            RuntimeError('consumer failed')
        )
        claim_outbox_events(1)

        with self.assertRaises(RuntimeError):
            process_outbox_event(str(self.event.pk))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, IntegrationOutbox.Status.FAILED)

    def test_future_event_is_not_claimed(self):
        self.event.available_at = timezone.now() + timedelta(minutes=5)
        self.event.save(update_fields=['available_at'])
        self.assertEqual(claim_outbox_events(1), [])

    @skipUnlessDBFeature('has_select_for_update_skip_locked')
    def test_two_workers_claim_event_only_once(self):
        def claim():
            close_old_connections()
            try:
                return [str(event.pk) for event in claim_outbox_events(1)]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))

        self.assertEqual(sum(len(result) for result in results), 1)
