"""Add reversible approval cycles and small group schedule reminders."""
from pathlib import Path
import sqlite3
import time
import ops_store as store
from ops_migrate4 import migrate as migrate4


def migrate():
    previous=migrate4()
    with store.connect() as con:
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions in ({1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):return previous
        if versions!={1,2,3,4}:raise ValueError('数据库版本未知，停止升级。')
    saved=store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=5').fetchone():return saved
        for statement in Path(__file__).with_name('ops_schema5.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():con.execute(statement)
        con.execute('INSERT INTO ops_migrations VALUES(5,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('关联检查失败，已回滚。')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():raise ValueError('找不到现有数据库，不会创建新数据库。')
        saved=migrate()
        print('UPGRADE OK / 界面与撤销审核已升级，账号和数据保留。')
        if saved:print('BACKUP:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error);raise SystemExit(1)
