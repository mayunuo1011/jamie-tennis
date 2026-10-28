"""Create and verify a consistent SQLite snapshot without changing the source."""
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


def backup_database(project_dir):
    project_dir = Path(project_dir).resolve()
    source = project_dir / 'tennis.db'
    if not source.is_file():
        raise FileNotFoundError('找不到 tennis.db，请先启动应用，或把备份文件放到应用文件夹。')
    folder = project_dir / 'backups'
    folder.mkdir(exist_ok=True)
    target = folder / f"tennis_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('备份完整性检查未通过。')
                if destination.execute('PRAGMA foreign_key_check').fetchall():
                    raise RuntimeError('备份中发现关联错误，请检查原数据。')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = backup_database(Path(__file__).parent)
        print(f'备份成功：{result}')
    except Exception as error:
        print(f'备份失败：{error}')
        raise SystemExit(1)
