"""Explicit additive calendar migration. Never create aliases for real people automatically."""
from pathlib import Path
import sqlite3
import time
import ops_store as store
from ops_migrate6 import migrate as migrate6


def migrate():
    previous=migrate6()
    with store.connect() as con:
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions==set(range(1,8)):return previous
        if versions!=set(range(1,7)):raise ValueError('数据库版本未知，停止升级。')
    saved=store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=7').fetchone():return saved
        for statement in Path(__file__).with_name('ops_schema7.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():con.execute(statement)
        con.execute('INSERT INTO ops_migrations VALUES(7,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('关联检查失败，已回滚。')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():raise ValueError('找不到现用数据库，不创建空数据库。')
        saved=migrate()
        print('UPGRADE OK / 学员周日历、账号直入和提前取消已升级。')
        if saved:print('BACKUP:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error);raise SystemExit(1)
