"""Preserve existing records; permit absence debt and add student booking tables."""
from pathlib import Path
import re
import sqlite3
import time
import ops_store as store
from ops_migrate5 import migrate as migrate5


def migrate():
    previous=migrate5()
    with store.connect() as con:
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions in (set(range(1,7)),set(range(1,8))):return previous
        if versions!=set(range(1,6)):raise ValueError('数据库版本未知，停止升级。')
    saved=store.backup()
    with store.connect() as con:
        # SQLite's documented table-rebuild procedure: disable FK enforcement BEFORE
        # BEGIN, leave referencing tables untouched, and check all FKs before commit.
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=6').fetchone():return saved
        sql=con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='packages'").fetchone()[0]
        sql,n=re.subn(r'CHECK\s*\(\s*remaining\s*>=\s*0\s*\)', '',sql,flags=re.I)
        if n!=1:raise ValueError('课包结构与预期不同，停止升级并保留原表。')
        sql,n=re.subn(r'CHECK\s*\(\s*purchased\s*>\s*0\s*\)', 'CHECK(purchased>=0)',sql,flags=re.I)
        if n!=1:raise ValueError('购买数量约束与预期不同，停止升级。')
        dependencies=[r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE tbl_name='packages' AND type IN ('index','trigger') AND sql IS NOT NULL")]
        sql=re.sub(r'CREATE TABLE\s+["`\[]?packages["`\]]?', 'CREATE TABLE packages_v6',sql,count=1,flags=re.I)
        con.execute(sql)
        con.execute('INSERT INTO packages_v6 SELECT * FROM packages')
        con.execute('DROP TABLE packages')
        con.execute('ALTER TABLE packages_v6 RENAME TO packages')
        for statement in dependencies:con.execute(statement)
        for statement in Path(__file__).with_name('ops_schema6.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():con.execute(statement)
        con.execute('INSERT INTO ops_migrations VALUES(6,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('外键检查失败，已回滚。')
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('完整性检查失败，已回滚。')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():raise ValueError('找不到现有数据库，不会创建空数据库。')
        saved=migrate()
        print('UPGRADE OK / 预约、课时占用和自动缺课扣课已开启。没有补扣历史缺课。')
        if saved:print('BACKUP:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error);raise SystemExit(1)
