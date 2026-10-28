"""Explicit additive migration; never run automatically on a page request."""
from pathlib import Path
import sqlite3
import time
import ops_store as store


def migrate():
    saved1=store.migrate()
    with store.connect() as con:
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions in ({1,2},{1,2,3},{1,2,3,4},{1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):return saved1
        if versions!={1}:raise RuntimeError('迁移版本不正确，停止升级。')
    saved=store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=2').fetchone():return saved
        for statement in Path(__file__).with_name('ops_schema2.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():con.execute(statement)
        con.execute('INSERT INTO ops_migrations VALUES(2,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise RuntimeError('外键检查未通过。')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():
            raise ValueError('Existing preview database not found. Copy this update into your existing preview folder first. No database was created.')
        saved=migrate()
        print('UPGRADE OK - Accounts, passwords and records are preserved.')
        if saved:print('Backup:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error)
        raise SystemExit(1)
