"""Independent preview DB; preserve legacy schema and data through additive migration."""
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import time
from ops_filelock import database_gate

DB_PATH=Path(os.environ.get('JAMIE_DB_PATH',str(Path(__file__).resolve().parent/'data'/'jamie-preview.db'))).resolve()


def configure(path):
    global DB_PATH
    DB_PATH=Path(path).resolve()


def cloud_enabled():return bool(os.environ.get('JAMIE_DATABASE_URL'))


def available():return cloud_enabled() or DB_PATH.is_file()


@contextmanager
def connect():
    if cloud_enabled():
        from pg_store import connect as pg_connect
        with pg_connect() as con:yield con
        return
    with database_gate(DB_PATH):
        con=sqlite3.connect(DB_PATH,timeout=15)
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def backup(prefix='before_multirole'):
    if cloud_enabled():raise RuntimeError('云端请使用独立导出备份工具，不使用本地SQLite备份。')
    if not DB_PATH.is_file():raise FileNotFoundError('数据库不存在，不会创建空数据库。')
    if prefix not in ('before_multirole','manual','before_restore'):raise ValueError('备份类型无效。')
    folder=DB_PATH.parent/'backups'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/f"{prefix}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S_%fZ}.db"
    try:
        with closing(sqlite3.connect(DB_PATH.as_uri()+'?mode=ro',uri=True)) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise RuntimeError('备份检查失败，停止迁移。')
                if destination.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('备份外键检查失败。')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return str(target)


def migrate():
    if cloud_enabled():raise RuntimeError('云端结构变更必须通过迁移工具显式执行。')
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    existed=DB_PATH.exists() and DB_PATH.stat().st_size>0
    if existed:
        with connect() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='ops_migrations'").fetchone():
                versions=[r[0] for r in con.execute('SELECT version FROM ops_migrations')]
                if set(versions) in ({1},{1,2},{1,2,3},{1,2,3,4},{1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):return None
                raise RuntimeError('数据库迁移版本未知，停止升级。')
    saved=backup() if existed else None
    # Reuse the original schema and existing coach-workspace migration.
    import user_store as legacy_store
    previous=legacy_store.DB_PATH
    try:
        legacy_store.configure(DB_PATH)
        legacy_store.initialize()
    finally:
        legacy_store.configure(previous)
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # Another process may have completed while we were backing up.
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='ops_migrations'").fetchone():
            if con.execute('SELECT 1 FROM ops_migrations WHERE version=1').fetchone():return saved
            raise RuntimeError('检测到未完成的迁移。')
        sql=(Path(__file__).with_name('ops_schema.sql')).read_text(encoding='utf-8')
        for statement in sql.split(';'):
            if statement.strip():con.execute(statement)
        for table in ('lessons','packages'):
            columns={r['name'] for r in con.execute(f'PRAGMA table_info({table})')}
            if 'coach_id' not in columns:
                con.execute(f'ALTER TABLE {table} ADD COLUMN coach_id INTEGER REFERENCES coaches(id)')
            con.execute(f'CREATE INDEX {table}_coach ON {table}(coach_id)')
        con.execute('INSERT INTO ops_migrations VALUES(1,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('外键检查未通过，新增迁移已回滚。')
    return saved
