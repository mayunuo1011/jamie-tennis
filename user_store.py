"""SQLite access and additive migration for the coach-facing workspace."""
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

import database as legacy

DB_PATH = Path(os.environ.get('TENNIS_USER_DB', str(Path(__file__).resolve().parent / 'tennis.db')))


def configure(path):
    global DB_PATH
    DB_PATH = Path(path).resolve()
    legacy.DB_PATH = DB_PATH


configure(DB_PATH)


@contextmanager
def connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def rows(sql, values=()):
    with connect() as connection:
        return [dict(row) for row in connection.execute(sql, values)]


STUDENT_FIELDS = {'joined_at': 'TEXT', 'starred': 'INTEGER NOT NULL DEFAULT 0'}
LESSON_FIELDS = {
    'checkin_done': 'INTEGER NOT NULL DEFAULT 0',
    'checked_in_at': 'TEXT',
    'cancellation_24h': 'INTEGER',
    'original_lesson_id': 'INTEGER REFERENCES lessons(id)',
    'reschedule_pending': 'INTEGER NOT NULL DEFAULT 0',
    'change_reason': "TEXT NOT NULL DEFAULT ''",
    'proposed_start': 'TEXT',
    'proposed_end': 'TEXT',
    'proposed_location': 'TEXT',
}


def initialize():
    """Back up before first migration, then add fields without altering credits."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    exists = DB_PATH.is_file() and DB_PATH.stat().st_size > 0
    needs_upgrade = True
    if exists:
        with connect() as connection:
            student_columns = {r['name'] for r in connection.execute('PRAGMA table_info(students)')}
            lesson_columns = {r['name'] for r in connection.execute('PRAGMA table_info(lessons)')}
            has_notes = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_notes'").fetchone()
            needs_upgrade = not (set(STUDENT_FIELDS) <= student_columns and set(LESSON_FIELDS) <= lesson_columns and has_notes)
        if needs_upgrade:
            folder = DB_PATH.parent / 'backups'
            folder.mkdir(exist_ok=True)
            backup = folder / f"before_user_workspace_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
            with closing(sqlite3.connect(DB_PATH.as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(backup)) as destination:
                    origin.backup(destination)
                    if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('升级前备份校验失败，未升级。')
    legacy.init_db()
    legacy.init_lessons()
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for table, fields in [('students', STUDENT_FIELDS), ('lessons', LESSON_FIELDS)]:
            existing_columns = {r['name'] for r in connection.execute(f'PRAGMA table_info({table})')}
            new_checkin_field = table == 'lessons' and 'checkin_done' not in existing_columns
            for field, definition in fields.items():
                if field not in existing_columns:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
            if new_checkin_field:
                connection.execute("UPDATE lessons SET checkin_done=1 WHERE status='已完成' AND (trim(note)!='' OR trim(next_goal)!='')")
        connection.execute("CREATE TABLE IF NOT EXISTS daily_notes(day TEXT PRIMARY KEY,body TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL)")
        connection.execute('CREATE INDEX IF NOT EXISTS user_lessons_start ON lessons(starts_at)')
    return str(backup) if backup else None
