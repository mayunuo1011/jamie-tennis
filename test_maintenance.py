"""Only disposable databases and fictional accounts. Never Desktop data."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import ops_auth as auth
import ops_maintenance as tool
import ops_service as service
import ops_store as store
from ops_filelock import database_gate
import test_ops as fixture
from test_ops2 import NOTE, GOAL


def digest(path):
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as con:
        return hashlib.sha256('\n'.join(con.iterdump()).encode()).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class MaintenanceTests(unittest.TestCase):
    setUp = fixture.OpsTests.setUp
    tearDown = fixture.OpsTests.tearDown

    def backup(self):
        return tool.create_backup()

    def restore(self, saved):
        return tool.restore_backup(saved, confirmed=True, port=free_port())

    def test_01_manual_backup_is_consistent_and_does_not_change_source(self):
        before = digest(store.DB_PATH)
        saved = self.backup()
        self.assertEqual(digest(saved), before)
        self.assertEqual(digest(store.DB_PATH), before)
        self.assertTrue(saved.name.startswith('manual_'))
        self.assertTrue(tool._verify_manifest(saved))
        self.assertEqual(tool.validate_database(saved)['schema'], 3)

    def test_02_multiple_backups_are_distinct_and_listed_newest_first(self):
        first, second = self.backup(), self.backup()
        self.assertNotEqual(first, second)
        self.assertEqual(tool.list_backups()[:2], [second, first])

    def test_03_missing_database_never_creates_one(self):
        missing = Path(self.temp.name) / 'missing' / 'no.db'
        store.configure(missing)
        for action in [tool.create_backup, tool.list_backups, lambda: tool.validate_database(missing)]:
            with self.assertRaises(ValueError):
                action()
        self.assertFalse(missing.parent.exists())

    def test_04_old_or_other_sqlite_rejected_unchanged(self):
        other = Path(self.temp.name) / 'old.db'
        with closing(sqlite3.connect(other)) as con:
            con.execute('CREATE TABLE old_data (id INTEGER)')
        current = digest(store.DB_PATH)
        with self.assertRaises(ValueError):
            self.restore(other)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_05_corrupt_backup_rejected_before_replace(self):
        bad = Path(self.temp.name) / 'bad.db'
        bad.write_bytes(b'not a sqlite database')
        current = digest(store.DB_PATH)
        with self.assertRaises(sqlite3.DatabaseError):
            self.restore(bad)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_06_manifest_mismatch_rejected(self):
        saved = self.backup()
        with closing(sqlite3.connect(saved)) as con:
            con.execute("UPDATE students SET name='虚拟备份已改变'")
            con.commit()
        current = digest(store.DB_PATH)
        with self.assertRaisesRegex(ValueError, '校验文件'):
            self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_07_explicit_confirmation_required(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with self.assertRaises(ValueError):
            tool.restore_backup(saved)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_08_cannot_restore_from_current_database(self):
        with self.assertRaises(ValueError):
            self.restore(store.DB_PATH)

    def test_09_restore_preserves_profiles_credits_feedback_roles_and_logs(self):
        sid = self.student
        own = service.student_detail(self.tokens['luo'], sid)
        service.save_student(self.tokens['luo'], '虚拟学员', pain_points='虚拟私有痛点', sid=sid,
                             coach_id=self.coaches['luo'], expected_revision=own['revision'])
        service.check_in(self.tokens['luo'], self.lids['luo'], NOTE, GOAL, 0)
        with store.connect() as con:
            con.execute("INSERT INTO packages(student_id,coach_id,kind,purchased_at,purchased,remaining) VALUES(?,?,'私教','2026-10-20',10,7)", (sid, self.coaches['luo']))
        saved = self.backup()
        with store.connect() as con:
            con.execute("UPDATE students SET name='虚拟后续错误修改'")
            con.execute('UPDATE packages SET remaining=1')
        before_restore = digest(store.DB_PATH)
        result = self.restore(saved)
        self.assertEqual(digest(result['safety_backup']), before_restore)
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT name FROM students WHERE id=?', (sid,)).fetchone()[0], '虚拟学员')
            self.assertEqual(con.execute('SELECT remaining FROM packages').fetchone()[0], 7)
            self.assertEqual(con.execute('SELECT pain_points FROM coach_student_profiles').fetchone()[0], '虚拟私有痛点')
            self.assertEqual(con.execute('SELECT count(*) FROM feedback_submissions').fetchone()[0], 1)
            self.assertEqual(con.execute('SELECT count(*) FROM auth_sessions').fetchone()[0], 0)
            row = con.execute("SELECT actor_id,details FROM ops_audit WHERE action='local_database_restore'").fetchone()
            self.assertIsNone(row['actor_id'])
            self.assertTrue(json.loads(row['details'])['sessions_revoked'])
        for username, role in [('luo', 'coach'), ('jamie', 'operator'), ('admin', 'system_admin')]:
            token = auth.login(username, fixture.PASSWORD)
            self.assertEqual(auth.current_user(token)['role'], role)
        with self.assertRaises(auth.AuthenticationError):
            auth.current_user(self.tokens['luo'])
        logs = [json.loads(line) for line in (store.DB_PATH.parent/'maintenance-log.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertIn('restore_started', [r['action'] for r in logs])
        self.assertEqual(logs[-1]['action'], 'restore_completed')

    def test_10_live_server_port_blocks_restore_without_modification(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen()
            with self.assertRaisesRegex(ValueError, '端口'):
                tool.restore_backup(saved, confirmed=True, port=server.getsockname()[1])
        self.assertEqual(digest(store.DB_PATH), current)

    def test_11_failed_safety_backup_keeps_current_database(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with patch('ops_store.backup', side_effect=OSError('simulated disk full')):
            with self.assertRaises(OSError):
                self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_12_failed_staged_audit_keeps_current_database(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with patch('ops_auth.audit', side_effect=RuntimeError('simulated audit failure')):
            with self.assertRaises(RuntimeError):
                self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)
        self.assertTrue(list(store.DB_PATH.parent.glob('backups/before_restore_*.db')))

    def test_13_failed_atomic_replace_keeps_current_database(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with patch('ops_maintenance._replace', side_effect=PermissionError('simulated busy file')):
            with self.assertRaises(PermissionError):
                self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)
        self.assertFalse(list(store.DB_PATH.parent.glob('.restore-*.db')))

    def test_14_failed_post_restore_check_rolls_back(self):
        saved = self.backup()
        with store.connect() as con:
            con.execute("UPDATE students SET name='虚拟恢复前状态'")
        current = digest(store.DB_PATH)
        original = tool.validate_database
        checks = 0
        def fail_after_apply(path):
            nonlocal checks
            if Path(path).resolve() == store.DB_PATH:
                checks += 1
                if checks == 2:
                    raise ValueError('simulated post restore validation failure')
            return original(path)
        with patch('ops_maintenance.validate_database', side_effect=fail_after_apply):
            with self.assertRaisesRegex(RuntimeError, '已回到'):
                self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_15_future_schema_rejected(self):
        saved = self.backup()
        saved.with_suffix('.db.json').unlink()
        with closing(sqlite3.connect(saved)) as con:
            con.execute('INSERT INTO ops_migrations VALUES(99,0)')
            con.commit()
        current = digest(store.DB_PATH)
        with self.assertRaisesRegex(ValueError, '版本3'):
            self.restore(saved)
        self.assertEqual(digest(store.DB_PATH), current)

    def test_16_earlier_migration_backup_without_manifest_supported_if_v3(self):
        saved = self.backup()
        saved.with_suffix('.db.json').unlink()
        self.assertFalse(tool._verify_manifest(saved))
        self.assertTrue(self.restore(saved)['safety_backup'])

    def test_17_wal_in_use_rejected_without_deleting_sidecars(self):
        saved = self.backup()
        with closing(sqlite3.connect(store.DB_PATH)) as editor:
            editor.execute('PRAGMA journal_mode=WAL')
            editor.execute("UPDATE students SET name='虚拟编辑器改动'")
            editor.commit()
            current = digest(store.DB_PATH)
            with self.assertRaisesRegex(ValueError, '日志文件'):
                self.restore(saved)
            self.assertTrue(Path(str(store.DB_PATH)+'-wal').exists())
            self.assertEqual(digest(store.DB_PATH), current)

    def test_18_backup_metadata_and_external_log_do_not_contain_secrets(self):
        saved = self.backup()
        text = saved.with_suffix('.db.json').read_text(encoding='utf-8') + (store.DB_PATH.parent/'maintenance-log.jsonl').read_text(encoding='utf-8')
        for value in [fixture.PASSWORD, self.tokens['luo'], '虚拟联系方式', '虚拟学员']:
            self.assertNotIn(value, text)

    def test_19_cli_cancel_never_restores(self):
        saved = self.backup()
        current = digest(store.DB_PATH)
        with patch('builtins.input', side_effect=['1', 'no']), patch('builtins.print'):
            tool.main(['restore'])
        self.assertEqual(digest(store.DB_PATH), current)

    def test_20_no_active_admin_backup_rejected(self):
        with store.connect() as con:
            con.execute("UPDATE accounts SET active=0 WHERE role='system_admin'")
        with self.assertRaisesRegex(ValueError, '系统管理员'):
            self.backup()

    def test_21_check_cli_does_not_change_database(self):
        before = digest(store.DB_PATH)
        with patch('builtins.print') as output:
            tool.main(['check'])
        self.assertTrue(any('CHECK OK' in str(c) for c in output.call_args_list))
        self.assertEqual(digest(store.DB_PATH), before)

    def test_22_lock_is_cross_process_and_released_on_exit(self):
        script = "from ops_filelock import database_gate; import sys;\nwith database_gate(sys.argv[1],timeout=0.1): print('LOCK OK')"
        with database_gate(store.DB_PATH):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(store.DB_PATH)],
                                    cwd=Path(__file__).parent, capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('LOCK OK', result.stdout)
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(store.DB_PATH)],
                                cwd=Path(__file__).parent, capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LOCK OK', result.stdout)

    def test_23_final_external_log_failure_reports_restore_success_with_warning(self):
        saved = self.backup()
        original = tool._event
        def fail_completion(action, **details):
            if action == 'restore_completed':
                raise OSError('simulated log device failure')
            return original(action, **details)
        with patch('ops_maintenance._event', side_effect=fail_completion):
            result = self.restore(saved)
        self.assertIn('恢复已完成', result['warning'])
        with store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM ops_audit WHERE action='local_database_restore'").fetchone()[0], 1)

    def test_24_double_failure_preserves_recovery_materials_and_explicit_error(self):
        saved = self.backup()
        original_check = tool.validate_database
        original_replace = tool._replace
        checks = 0
        replacements = 0
        def fail_check(path):
            nonlocal checks
            if Path(path).resolve() == store.DB_PATH:
                checks += 1
                if checks == 2:
                    raise ValueError('simulated post restore failure')
            return original_check(path)
        def fail_rollback(stage, target):
            nonlocal replacements
            replacements += 1
            if replacements == 2:
                raise PermissionError('simulated rollback file busy')
            return original_replace(stage, target)
        with patch('ops_maintenance.validate_database', side_effect=fail_check), patch('ops_maintenance._replace', side_effect=fail_rollback):
            with self.assertRaisesRegex(RuntimeError, '安全备份'):
                self.restore(saved)
        self.assertTrue(list(store.DB_PATH.parent.glob('backups/before_restore_*.db')))
        self.assertTrue(list(store.DB_PATH.parent.glob('.restore-*.db')))

    def test_25_real_cli_backup_and_confirmed_restore(self):
        env = dict(os.environ, JAMIE_DB_PATH=str(store.DB_PATH), PYTHONDONTWRITEBYTECODE='1')
        script = Path(__file__).with_name('ops_maintenance.py')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'backup'], env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('BACKUP OK', result.stdout)
        with store.connect() as con:
            con.execute("UPDATE students SET name='虚拟命令行错误输入'")
        result = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'restore', '--port', str(free_port())],
                                input='1\nRESTORE\n', env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('RESTORE OK', result.stdout)
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT name FROM students WHERE id=?', (self.student,)).fetchone()[0], '虚拟学员')

    def test_26_real_cli_missing_database_fails_without_creating_it(self):
        missing = Path(self.temp.name) / 'absent' / 'missing.db'
        env = dict(os.environ, JAMIE_DB_PATH=str(missing), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('ops_maintenance.py')), 'backup'],
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAILED', result.stdout)
        self.assertFalse(missing.parent.exists())


if __name__ == '__main__':
    unittest.main()
