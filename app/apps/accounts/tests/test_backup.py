import os
import stat
import tempfile
import subprocess
from pathlib import Path
from unittest import mock

from django.test import TestCase


MOCK_RCLONE_SUCCESS = """#!/bin/bash
case "$1" in
    mkdir) exit 0 ;;
    copyto) exit 0 ;;
    listremotes) echo 'gdrive:' ;;
    lsf) echo "querolink_2026-01-01_000000.dump" ;;
    delete)
        if echo "$*" | grep -q "dry-run"; then
            echo "No files to delete"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
"""


MOCK_PG_DUMP_SUCCESS = """#!/bin/bash
# Output file is the last argument (after -f)
echo "mock_dump_data" > "${@: -1}"
exit 0
"""


class DailyBackupTaskTest(TestCase):
    @mock.patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout="[00:00:00] ===== QUEROLINK BACKUP =====\n[00:00:01] ===== BACKUP CONCLUIDO =====",
            stderr="",
        )
        from app.apps.accounts.tasks import daily_backup

        daily_backup()

        mock_run.assert_called_once_with(
            ['/app/scripts/backup.sh'],
            capture_output=True, text=True, timeout=900,
            check=True,
        )

    @mock.patch("subprocess.run")
    def test_called_process_error_raises(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=['/app/scripts/backup.sh'],
            output="stdout parcial",
            stderr="FATAL: erro simulado",
        )
        from app.apps.accounts.tasks import daily_backup

        with self.assertRaises(subprocess.CalledProcessError):
            daily_backup()

    @mock.patch("subprocess.run")
    def test_timeout_expired_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=['/app/scripts/backup.sh'],
            timeout=300,
        )
        from app.apps.accounts.tasks import daily_backup

        with self.assertRaises(subprocess.TimeoutExpired):
            daily_backup()

    @mock.patch("subprocess.run")
    def test_file_not_found_raises(self, mock_run):
        mock_run.side_effect = FileNotFoundError("Script not found")
        from app.apps.accounts.tasks import daily_backup

        with self.assertRaises(FileNotFoundError):
            daily_backup()


class BackupScriptParsingTest(TestCase):
    """Testa a logica de parse de DATABASE_URL usada pelo backup.sh.
    Nao acessa banco real nem Google Drive real."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpdir = tempfile.mkdtemp(prefix="ql_backup_test_")

    def _make_bin(self, name, content):
        bin_path = Path(self.tmpdir) / name
        bin_path.write_text("#!/bin/bash\n" + content)
        bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
        return str(bin_path)

    def _run_backup_script(self, extra_env=None):
        env = {
            "PATH": self.tmpdir + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmpdir,
            "BACKUP_DIR": self.tmpdir,
            "GDRIVE_REMOTE": "gdrive",
            "GDRIVE_PATH": "test-backups",
            "LOCAL_RETENTION_DAYS": "1",
            "REMOTE_RETENTION_DAYS": "1",
        }
        if extra_env:
            env.update(extra_env)
        if "DATABASE_URL" in os.environ:
            env["DATABASE_URL"] = os.environ["DATABASE_URL"]

        script_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "..", "scripts", "backup.sh"
        )
        return subprocess.run(
            ["bash", script_path],
            capture_output=True, text=True, timeout=30,
            env=env,
        )

    def _setup_db_url(self, url):
        os.environ["DATABASE_URL"] = url

    def _teardown_db_url(self):
        os.environ.pop("DATABASE_URL", None)

    def _make_success_env(self):
        return {
            "PG_DUMP_BIN": self._make_bin("mock_pg_dump", MOCK_PG_DUMP_SUCCESS),
            "RCLONE_BIN": self._make_bin("mock_rclone", MOCK_RCLONE_SUCCESS),
        }

    def test_parse_standard_url(self):
        self._setup_db_url("postgres://user:pass123@dbhost:5433/mydb")
        try:
            result = self._run_backup_script(extra_env=self._make_success_env())
        finally:
            self._teardown_db_url()
        self.assertEqual(result.returncode, 0, f"backup failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}")

    def test_parse_url_with_special_chars(self):
        self._setup_db_url("postgres://us%40r:p%3Ass@host:5432/dbname")
        try:
            result = self._run_backup_script(extra_env=self._make_success_env())
        finally:
            self._teardown_db_url()
        self.assertEqual(result.returncode, 0, f"backup failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}")

    def test_missing_database_url_fails(self):
        result = self._run_backup_script(extra_env={})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL", result.stdout + result.stderr)

    def test_pg_dump_failure_fails_script(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        fail_pg = self._make_bin("mock_pg_dump_fail", "echo 'pg_dump error' >&2; exit 1")
        mock_rc = self._make_bin("mock_rclone", MOCK_RCLONE_SUCCESS)
        try:
            result = self._run_backup_script(extra_env={
                "PG_DUMP_BIN": fail_pg,
                "RCLONE_BIN": mock_rc,
            })
        finally:
            self._teardown_db_url()
        self.assertNotEqual(result.returncode, 0)

    def test_empty_dump_file_fails(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        empty_pg = self._make_bin("mock_pg_dump_empty", "touch \"${@: -1}\"; exit 0")
        mock_rc = self._make_bin("mock_rclone", MOCK_RCLONE_SUCCESS)
        try:
            result = self._run_backup_script(extra_env={
                "PG_DUMP_BIN": empty_pg,
                "RCLONE_BIN": mock_rc,
            })
        finally:
            self._teardown_db_url()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vazio", result.stdout + result.stderr)

    def test_rclone_upload_failure_fails_script(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        pg = self._make_bin("mock_pg_dump", MOCK_PG_DUMP_SUCCESS)
        rc_fail = self._make_bin("mock_rclone_fail", """#!/bin/bash
case "$1" in
    mkdir) exit 0 ;;
    copyto) echo 'upload failed' >&2; exit 1 ;;
    listremotes) echo 'gdrive:' ;;
    *) exit 0 ;;
esac
""")
        try:
            result = self._run_backup_script(extra_env={
                "PG_DUMP_BIN": pg,
                "RCLONE_BIN": rc_fail,
            })
        finally:
            self._teardown_db_url()
        self.assertNotEqual(result.returncode, 0)

    def test_remote_verification_failure_fails_script(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        pg = self._make_bin("mock_pg_dump", MOCK_PG_DUMP_SUCCESS)
        rc_empty_lsf = self._make_bin("mock_rclone_empty", """#!/bin/bash
case "$1" in
    mkdir) exit 0 ;;
    copyto) exit 0 ;;
    listremotes) echo 'gdrive:' ;;
    lsf) echo "" ; exit 0 ;;
    *) exit 0 ;;
esac
""")
        try:
            result = self._run_backup_script(extra_env={
                "PG_DUMP_BIN": pg,
                "RCLONE_BIN": rc_empty_lsf,
            })
        finally:
            self._teardown_db_url()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NAO encontrado", result.stdout + result.stderr)

    def test_zero_old_backups_does_not_fail(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        pg = self._make_bin("mock_pg_dump", MOCK_PG_DUMP_SUCCESS)
        rc_no_old = self._make_bin("mock_rclone_no_old", """#!/bin/bash
case "$1" in
    mkdir) exit 0 ;;
    copyto) exit 0 ;;
    listremotes) echo 'gdrive:' ;;
    lsf) echo "some_querolink_file.dump" ;;
    delete)
        if echo "$*" | grep -q "dry-run"; then
            echo "No files to delete"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
""")
        try:
            result = self._run_backup_script(extra_env={
                "PG_DUMP_BIN": pg,
                "RCLONE_BIN": rc_no_old,
            })
        finally:
            self._teardown_db_url()
        self.assertEqual(result.returncode, 0, f"script failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}")

    def test_uses_dump_extension(self):
        self._setup_db_url("postgres://u:p@h:5432/db")
        try:
            result = self._run_backup_script(extra_env=self._make_success_env())
        finally:
            self._teardown_db_url()
        self.assertEqual(result.returncode, 0, f"script failed:\nSTDOUT={result.stdout}\nSTDERR={result.stderr}")

        dump_files = list(Path(self.tmpdir).glob("querolink_*.dump"))
        self.assertGreaterEqual(len(dump_files), 1, f"Should have created a .dump file in {self.tmpdir}. Contents: {list(Path(self.tmpdir).iterdir())}")
        for f in dump_files:
            self.assertTrue(f.name.endswith(".dump"), f"File should end with .dump: {f.name}")
