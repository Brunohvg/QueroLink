import os
import tempfile
from subprocess import CalledProcessError, CompletedProcess
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from app.apps.accounts.backup_ops import (
    redact_backup_output,
    run_backup_check,
    run_backup_now,
)


class BackupOpsTest(SimpleTestCase):
    def _settings(self, **overrides):
        data = {
            'rclone_bin': 'rclone',
            'remote': 'gdrive',
            'path': 'merito-backups',
            'script_path': '/tmp/missing-backup.sh',
            'check_timeout': 10,
            'backup_timeout': 20,
        }
        data.update(overrides)
        return data

    def test_redacts_google_tokens(self):
        text = (
            'access_token":"ya29.secret" '
            'refresh_token":"1//refreshsecret" '
            'plain'
        )
        redacted = redact_backup_output(text)
        self.assertNotIn('ya29.secret', redacted)
        self.assertNotIn('1//refreshsecret', redacted)
        self.assertIn('<redacted>', redacted)

    def test_backup_check_success(self):
        calls = []

        def runner(args, timeout):
            calls.append(args)
            if args[1] == 'listremotes':
                return CompletedProcess(args, 0, stdout='gdrive:\n')
            if args[1] == 'lsf':
                return CompletedProcess(args, 0, stdout=args[-1].replace('--include', ''))
            return CompletedProcess(args, 0, stdout='')

        steps = run_backup_check(settings=self._settings(), runner=runner)

        self.assertIn('rclone version', steps)
        self.assertIn('rclone copyto probe', steps)
        self.assertTrue(any(call[1] == 'deletefile' for call in calls))

    def test_backup_check_missing_remote_fails(self):
        def runner(args, timeout):
            if args[1] == 'listremotes':
                return CompletedProcess(args, 0, stdout='other:\n')
            return CompletedProcess(args, 0, stdout='')

        with self.assertRaisesMessage(RuntimeError, "Remote rclone 'gdrive' nao configurado"):
            run_backup_check(settings=self._settings(), runner=runner)

    def test_backup_check_write_probe_failure_fails(self):
        def runner(args, timeout):
            if args[1] == 'listremotes':
                return CompletedProcess(args, 0, stdout='gdrive:\n')
            if args[1] == 'copyto':
                raise CalledProcessError(1, args, output='', stderr='denied')
            return CompletedProcess(args, 0, stdout='')

        with self.assertRaises(CalledProcessError):
            run_backup_check(settings=self._settings(), runner=runner)

    def test_backup_now_missing_script_fails(self):
        with self.assertRaises(FileNotFoundError):
            run_backup_now(settings=self._settings())

    def test_backup_now_success(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as handle:
            handle.write('#!/bin/sh\n')
            script = handle.name
        try:
            result = run_backup_now(
                settings=self._settings(script_path=script),
                runner=lambda args, timeout: CompletedProcess(args, 0, stdout='ok\n'),
            )
        finally:
            os.unlink(script)

        self.assertEqual(result.stdout, 'ok\n')


class BackupCommandsTest(SimpleTestCase):
    @mock.patch('app.apps.accounts.management.commands.backup_check.run_backup_check')
    def test_backup_check_command_success(self, mocked):
        mocked.return_value = ['rclone version']
        call_command('backup_check')
        mocked.assert_called_once()

    @mock.patch('app.apps.accounts.management.commands.backup_check.run_backup_check')
    def test_backup_check_command_sanitizes_error(self, mocked):
        mocked.side_effect = RuntimeError('token ya29.secret failed')
        with self.assertRaises(CommandError) as ctx:
            call_command('backup_check')
        self.assertNotIn('ya29.secret', str(ctx.exception))

    @mock.patch('app.apps.accounts.management.commands.backup_now.run_backup_now')
    def test_backup_now_command_success(self, mocked):
        mocked.return_value = CompletedProcess(['backup'], 0, stdout='done\n')
        call_command('backup_now')
        mocked.assert_called_once()

    @mock.patch('app.apps.accounts.management.commands.backup_now.run_backup_now')
    def test_backup_now_command_sanitizes_error(self, mocked):
        mocked.side_effect = RuntimeError('refresh 1//secret failed')
        with self.assertRaises(CommandError) as ctx:
            call_command('backup_now')
        self.assertNotIn('1//secret', str(ctx.exception))
