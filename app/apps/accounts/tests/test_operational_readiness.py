from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase


class HealthCheckTests(TestCase):
    def test_health_endpoint_reports_operational_dependencies(self):
        response = self.client.get('/health/')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['database'], 'ok')
        self.assertIn(payload['redis'], ['ok', 'skipped'])
        self.assertIn(payload['celery'], ['ok', 'eager'])

    @mock.patch('app.config.health._check_celery_broker', return_value='ok')
    @mock.patch('app.config.health._check_redis', return_value='error')
    @mock.patch('app.config.health._check_database', return_value=True)
    def test_health_endpoint_returns_503_when_dependency_is_degraded(
        self,
        mock_database,
        mock_redis,
        mock_celery,
    ):
        response = self.client.get('/health/')

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(payload['status'], 'degraded')
        self.assertEqual(payload['database'], 'ok')
        self.assertEqual(payload['redis'], 'error')
        self.assertEqual(payload['celery'], 'ok')


class CeleryObservabilityTests(SimpleTestCase):
    def setUp(self):
        from app.config import celery_observability
        celery_observability._TASK_STARTS.clear()

    def test_observed_task_logs_start_and_success_with_duration(self):
        from app.config import celery_observability

        sender = type('TaskSender', (), {
            'name': 'app.apps.webhooks.tasks.process_pagarme_webhook',
        })()

        with mock.patch.object(celery_observability.logger, 'info') as mock_info:
            celery_observability.log_task_start(sender=sender, task_id='task-1')
            celery_observability.log_task_success(
                sender=sender,
                task_id='task-1',
                state='SUCCESS',
            )

        self.assertEqual(mock_info.call_count, 2)
        start_payload = mock_info.call_args_list[0].kwargs['extra']['structured']
        success_payload = mock_info.call_args_list[1].kwargs['extra']['structured']
        self.assertEqual(start_payload['event'], 'start')
        self.assertEqual(success_payload['event'], 'success')
        self.assertEqual(success_payload['task_name'], sender.name)
        self.assertGreaterEqual(success_payload['duration_ms'], 0)
        self.assertNotIn('args', success_payload)
        self.assertNotIn('kwargs', success_payload)

    def test_observed_task_failure_logs_traceback_without_arguments(self):
        from app.config import celery_observability

        sender = type('TaskSender', (), {
            'name': 'app.apps.notifications.tasks.send_whatsapp_notification',
        })()
        einfo = type('ExceptionInfo', (), {'traceback': 'Traceback test'})()

        celery_observability.log_task_start(sender=sender, task_id='task-2')
        with mock.patch.object(celery_observability.logger, 'error') as mock_error:
            celery_observability.log_task_failure(
                sender=sender,
                task_id='task-2',
                exception=RuntimeError('boom'),
                einfo=einfo,
            )

        payload = mock_error.call_args.kwargs['extra']['structured']
        self.assertEqual(payload['event'], 'failure')
        self.assertEqual(payload['exception_class'], 'RuntimeError')
        self.assertEqual(payload['traceback'], 'Traceback test')
        self.assertNotIn('args', payload)
        self.assertNotIn('kwargs', payload)


class ReadinessReportTests(SimpleTestCase):
    def test_readiness_report_contains_required_sections(self):
        report = Path(settings.BASE_DIR, 'READINESS_REPORT.md').read_text()

        required_sections = [
            '# Estado atual',
            '# Funcionalidades prontas',
            '# Segurança',
            '# Testes',
            '# Infraestrutura',
            '# Pendências',
            '# Checklist Produção',
        ]
        for section in required_sections:
            self.assertIn(section, report)
