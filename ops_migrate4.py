"""Add a credit journal without changing existing business records or ownership."""
from pathlib import Path
import sqlite3
import time
import ops_store as store
from ops_migrate3 import migrate as migrate3


def migrate():
    saved3 = migrate3()
    with store.connect() as con:
        versions = {r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions in ({1,2,3,4},{1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):return saved3
        if versions != {1,2,3}:raise ValueError('数据库版本未知，停止升级。')
    saved = store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=4').fetchone():return saved
        for sql in Path(__file__).with_name('ops_schema4.sql').read_text(encoding='utf-8').split(';'):
            if sql.strip():con.execute(sql)
        from ops_credits import _entry
        for p in con.execute('SELECT * FROM packages WHERE coach_id IS NOT NULL').fetchall():
            _entry(con,None,p,'opening',p['remaining'],reason='升级前余额快照；不推算历史使用情况')
        con.execute('INSERT INTO ops_migrations VALUES(4,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('关联检查失败，已回滚。')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():raise ValueError('找不到现有数据库，不会创建新数据库。')
        saved=migrate()
        print('UPGRADE OK / 课时管理已开启，已有账号和数据保留；没有自动补扣历史课程。')
        if saved:print('BACKUP:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error)
        raise SystemExit(1)
