import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backup_data import backup_database


class BackupTests(unittest.TestCase):
    def test_snapshot_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'tennis.db'
            with closing(sqlite3.connect(source)) as connection:
                connection.execute('CREATE TABLE example (name TEXT)')
                connection.execute('INSERT INTO example VALUES (?)', ('虚拟学员',))
                connection.commit()
            snapshot = backup_database(folder)
            self.assertTrue(snapshot.is_file())
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("UPDATE example SET name='changed'")
                connection.commit()
            with closing(sqlite3.connect(snapshot)) as connection:
                self.assertEqual(connection.execute('SELECT name FROM example').fetchone()[0], '虚拟学员')
                with closing(sqlite3.connect(Path(folder) / 'restored.db')) as restored:
                    connection.backup(restored)
                    self.assertEqual(restored.execute('SELECT name FROM example').fetchone()[0], '虚拟学员')

    def test_missing_database_does_not_create_empty_database(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                backup_database(folder)
            self.assertFalse((Path(folder) / 'tennis.db').exists())

    def test_multiple_backups_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(sqlite3.connect(Path(folder) / 'tennis.db')) as connection:
                connection.execute('CREATE TABLE example (id INTEGER)')
            first = backup_database(folder)
            second = backup_database(folder)
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists() and second.exists())
