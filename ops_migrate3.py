"""Back up and explicitly migrate the existing preview to the review workflow."""
from pathlib import Path
import sqlite3
import time
import ops_store as store
from ops_migrate2 import migrate as migrate2


def migrate():
    from ops_reviews import submit_current
    saved2=migrate2()
    with store.connect() as con:
        versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
        if versions in ({1,2,3},{1,2,3,4},{1,2,3,4,5},{1,2,3,4,5,6},{1,2,3,4,5,6,7}):return saved2
        if versions!={1,2}:raise RuntimeError('Unexpected migration version.')
    saved=store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM ops_migrations WHERE version=3').fetchone():return saved
        for statement in Path(__file__).with_name('ops_schema3.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():con.execute(statement)
        candidates=con.execute("SELECT id FROM lessons WHERE archived=0 AND checkin_done=1 AND coach_id IS NOT NULL AND status NOT IN ('已取消','已改期')").fetchall()
        for row in candidates:
            try:submit_current(con,row['id'],None,source='migration')
            except ValueError:
                # Preserve old incomplete feedback without inventing a submission.
                continue
        con.execute('INSERT INTO ops_migrations VALUES(3,?)',(int(time.time()),))
        if con.execute('PRAGMA foreign_key_check').fetchone():raise RuntimeError('Foreign key validation failed.')
    return saved


if __name__=='__main__':
    try:
        if not store.DB_PATH.is_file():raise ValueError('Preview database missing. Copy the update into your existing preview folder first.')
        saved=migrate()
        print('UPGRADE OK - Review inbox ready. No credits deducted. Accounts and passwords preserved.')
        if saved:print('Backup:',saved)
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('UPGRADE FAILED:',error)
        raise SystemExit(1)
