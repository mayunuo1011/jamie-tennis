"""Local OS-authorized maintenance. No Web route, schema migration or sample data."""
import argparse
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime, timezone
from getpass import getuser
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import uuid
from zoneinfo import ZoneInfo

import ops_store as store
from ops_filelock import database_gate

REQUIRED = {
    'ops_migrations': {'version', 'applied_at'},
    'accounts': {'id', 'username', 'password_hash', 'role', 'active', 'coach_id'},
    'coaches': {'id', 'name'},
    'students': {'id', 'name', 'revision'},
    'lessons': {'id', 'student_id', 'coach_id', 'kind', 'revision', 'archived', 'checkin_done', 'charged'},
    'packages': {'id', 'student_id', 'coach_id', 'kind', 'remaining'},
    'student_coaches': {'student_id', 'coach_id'},
    'coach_student_profiles': {'student_id', 'coach_id', 'remarks'},
    'auth_sessions': {'token_hash', 'account_id'},
    'login_limits': {'username'},
    'account_notes': {'account_id', 'day', 'body'},
    'student_preferences': {'account_id', 'student_id', 'starred'},
    'ops_requests': {'account_id', 'request_key', 'lesson_id'},
    'ops_audit': {'id', 'actor_id', 'action', 'entity', 'details', 'created_at'},
    'feedback_submissions': {'id', 'lesson_id', 'state', 'is_current'},
    'feedback_reads': {'submission_id', 'account_id'},
}


def _path(path):
    result = Path(path).expanduser().resolve()
    if not result.is_file():
        raise ValueError('找不到数据库文件；不会创建新数据库。')
    return result


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def validate_database(path):
    """Read-only, no pupil/account rows are returned or printed."""
    path = _path(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as con:
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('数据库完整性检查未通过。')
        if con.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('数据库关联检查未通过。')
        for table, columns in REQUIRED.items():
            actual = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
            if not columns <= actual:
                raise ValueError('不是兼容的新版审核数据库，缺少结构：' + table)
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions not in ({1,2,3},{1,2,3,4},{1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):
            raise ValueError('仅支持已升级的版本3至7数据库；不会自动升级或降级。')
        schema=max(versions)
        if schema>=4:
            for table,columns in {'credit_ledger':{'id','student_id','coach_id','kind','package_id','lesson_id','delta','action'},'credit_requests':{'account_id','request_key','fingerprint','package_id'}}.items():
                if not columns <= {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('课时管理结构缺失：'+table)
        if schema>=5:
            for table,columns in {'credit_ledger':{'active','reverses_id'},'group_reminders':{'id','coach_id','name','starts_at','ends_at','revision'},'group_members':{'group_id','student_id'}}.items():
                if not columns <= {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('界面版本结构缺失：'+table)
        if schema>=6:
            for table,columns in {'packages':{'is_debt'},'student_accounts':{'student_id','password_hash','active','must_change'},'student_sessions':{'token_hash','student_id','expires_at','last_seen'},'student_login_limits':{'username','failures','blocked_until'},'booking_slots':{'id','coach_id','kind','closed','revision'},'lessons':{'booking_slot_id'},'student_booking_requests':{'student_id','request_key','slot_id','lesson_id'}}.items():
                if not columns <= {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('预约版本结构缺失：'+table)
        if schema>=7:
            for table,columns in {'student_accounts':{'login_name'},'booking_slots':{'published'},'lessons':{'attendance_confirmed'}}.items():
                if not columns <= {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                    raise ValueError('日历版本结构缺失：'+table)
        if not con.execute("SELECT 1 FROM accounts WHERE role='system_admin' AND active=1").fetchone():
            raise ValueError('备份中没有启用的系统管理员账号，停止操作。')
    return {'path': str(path), 'schema': schema, 'integrity': 'ok'}


def _manifest(path, kind):
    result = {'format': 1, 'schema': validate_database(path)['schema'], 'kind': kind, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'file': path.name, 'sha256': _digest(path)}
    path.with_suffix('.db.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def _verify_manifest(path):
    manifest = path.with_suffix('.db.json')
    if not manifest.is_file():
        return False  # Earlier migration backups did not carry a digest file.
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if record.get('sha256') != _digest(path):
        raise ValueError('备份与校验文件不一致，停止恢复。请保留文件排查。')
    return True


def _event(action, **details):
    """External journal survives DB restore; no pupil details or secrets."""
    record = {'at_utc': datetime.now(timezone.utc).isoformat(), 'os_user': getuser(),
              'method': 'local_cli', 'action': action, **details}
    folder = store.DB_PATH.parent
    with (folder / 'maintenance-log.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def create_backup():
    _path(store.DB_PATH)
    with database_gate(store.DB_PATH):
        validate_database(store.DB_PATH)
        saved = Path(store.backup(prefix='manual'))
        validate_database(saved)
        _manifest(saved, 'manual')
        _event('backup_completed', backup=saved.name)
        return saved


def list_backups():
    _path(store.DB_PATH)
    folder = store.DB_PATH.parent / 'backups'
    return sorted((p for p in folder.glob('*.db') if p.is_file()), key=lambda p: p.stat().st_mtime_ns, reverse=True)


@contextmanager
def _stopped_server(port):
    """Reserve the configured local port until restore ends; never kill a server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as guard:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            guard.bind(('127.0.0.1', port))
        except OSError as error:
            raise ValueError(f'网站端口 {port} 仍被占用，请先关闭网站运行窗口，再恢复。') from error
        yield


def _snapshot(source, target):
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=3)) as origin:
        with closing(sqlite3.connect(target)) as destination:
            origin.backup(destination)


def _stage_audit(stage, operation_id, source, safety):
    # Reuse the current audit format without pretending a Web user performed it.
    import ops_auth as auth
    with closing(sqlite3.connect(stage)) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        con.execute('DELETE FROM auth_sessions')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=6').fetchone():
            con.execute('DELETE FROM student_sessions')
        auth.audit(con, None, 'local_database_restore', 'database', details={
            'method': 'local_cli', 'os_user': getuser(), 'operation_id': operation_id,
            'backup': source.name, 'before_restore': safety.name, 'sessions_revoked': True})
        con.commit()


def _replace(stage, target):
    # Same-directory staging means replace is a same-filesystem operation.
    with stage.open('r+b') as handle:
        os.fsync(handle.fileno())
    os.replace(stage, target)


def restore_backup(source, *, confirmed=False, port=8506, student_port=8507):
    if confirmed is not True:
        raise ValueError('必须明确确认恢复，未修改数据库。')
    target = _path(store.DB_PATH)
    source = _path(source)
    if source == target or os.path.samefile(source, target):
        raise ValueError('不能选择当前数据库自身作为恢复来源。')
    if source.parent == target.parent and source.name.startswith('.restore-'):
        raise ValueError('不能选择未完成的恢复临时文件。')
    operation_id = uuid.uuid4().hex
    with _stopped_server(port), database_gate(target, timeout=3), ExitStack() as extra_ports:
        # Valid source is prepared before any replacement or changes to current DB.
        _verify_manifest(source)
        fd, filename = tempfile.mkstemp(prefix='.restore-', suffix='.db', dir=target.parent)
        os.close(fd)
        stage = Path(filename)
        applied = False
        preserve_stage = False
        safety = None
        try:
            _snapshot(source, stage)
            source_info=validate_database(stage)
            # Current DB must be readable too; corrupt DB recovery is deliberately manual.
            target_info=validate_database(target)
            if source_info['schema']!=target_info['schema']:
                raise ValueError('来源和现用数据库版本不同，不能自动升级或降级恢复；请联系维护。')
            if target_info['schema']>=6 and student_port!=port:
                extra_ports.enter_context(_stopped_server(student_port))
            safety = Path(store.backup(prefix='before_restore'))
            validate_database(safety)
            _manifest(safety, 'before_restore')
            _stage_audit(stage, operation_id, source, safety)
            validate_database(stage)
            for suffix in ('-wal', '-shm', '-journal'):
                if Path(str(target) + suffix).exists():
                    raise ValueError('仍有数据库日志文件，请关闭所有数据库编辑器和网站后重试；不要手动删除日志。')
            _event('restore_started', operation_id=operation_id, backup=source.name, before_restore=safety.name)
            _replace(stage, target)
            applied = True
            try:
                validate_database(target)
            except Exception:
                # Recover the pre-restore state if post-apply checks unexpectedly fail.
                try:
                    shutil.copyfile(safety, stage)
                    _replace(stage, target)
                except Exception as rollback_error:
                    preserve_stage = True
                    raise RuntimeError('恢复后检查与自动回退未能完成，请停止使用网站并联系维护。恢复前安全备份：' + str(safety)) from rollback_error
                applied = False
                _event('restore_rolled_back', operation_id=operation_id, before_restore=safety.name)
                raise RuntimeError('恢复后检查失败，已回到恢复前数据库。安全备份已保留。')
            warning = ''
            try:
                _event('restore_completed', operation_id=operation_id, backup=source.name, before_restore=safety.name)
            except OSError:
                warning = '数据库恢复已完成，但外部完成日志写入失败；数据库内已有恢复操作记录。'
            return {'database': str(target), 'safety_backup': str(safety), 'warning': warning}
        except Exception:
            # Do not mask the original exception if journal storage itself failed.
            try:
                _event('restore_failed_after_apply' if applied else 'restore_not_applied', operation_id=operation_id,
                       before_restore=safety.name if safety else None)
            except OSError:
                pass
            raise
        finally:
            if not preserve_stage:
                stage.unlink(missing_ok=True)


def _choose_backup():
    backups = list_backups()
    if not backups:
        raise ValueError('没有找到备份。请先运行“备份新版数据”。')
    print('备份按时间从新到旧排列（显示英国时间）：')
    for i, path in enumerate(backups, 1):
        stamp = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo('Europe/London')).strftime('%Y-%m-%d %H:%M')
        print(f'{i}. {stamp}  {path.name}')
    value = input('输入要恢复的编号；直接回车取消：').strip()
    if not value:
        return None
    if not value.isdecimal() or not 1 <= int(value) <= len(backups):
        raise ValueError('编号无效，未恢复。')
    return backups[int(value)-1]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local Jamie Tennis backup / restore')
    parser.add_argument('action', choices=['backup', 'restore', 'list', 'check', 'folder'])
    parser.add_argument('--port', type=int, default=8506)
    args = parser.parse_args(argv)
    print('Jamie Tennis / 本机维护工具')
    print('DATABASE:', store.DB_PATH)
    if args.action == 'backup':
        saved = create_backup()
        print('BACKUP OK / 备份成功')
        print('SAVED:', saved)
    elif args.action == 'check':
        _path(store.DB_PATH)
        with database_gate(store.DB_PATH):
            info=validate_database(store.DB_PATH)
        print(f"CHECK OK / 数据库版本{info['schema']}，完整性与关联检查通过")
    elif args.action == 'list':
        for path in list_backups():
            print(path)
    elif args.action == 'folder':
        _path(store.DB_PATH)
        folder = store.DB_PATH.parent / 'backups'
        folder.mkdir(exist_ok=True)
        print('FOLDER:', folder)
        if os.name == 'nt':
            os.startfile(folder)
    else:
        print('恢复前必须关闭网站的黑色运行窗口，以及 DB Browser 等数据库编辑器。')
        selected = _choose_backup()
        if selected is None:
            print('CANCELLED / 已取消，未恢复')
            return
        validate_database(selected)
        _verify_manifest(selected)
        print('RESTORE FROM:', selected)
        print('RESTORE TO:', store.DB_PATH)
        print('恢复会回到备份时的全部数据及密码，备份之后的修改将被替换。')
        print('会先保留恢复前安全备份；恢复后所有人需要重新登录。')
        if input('确认恢复请输入 RESTORE；其他输入均取消：').strip() != 'RESTORE':
            print('CANCELLED / 已取消，未恢复')
            return
        result = restore_backup(selected, confirmed=True, port=args.port)
        print('RESTORE OK / 恢复完成，请重新启动网站并登录')
        print('SAFETY BACKUP:', result['safety_backup'])
        if result['warning']:
            print('WARNING:', result['warning'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        print('FAILED / 未完成:', error)
        raise SystemExit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nCANCELLED / 已取消')
        raise SystemExit(1)
