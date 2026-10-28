"""Local process gate. The lock file is permanent; the OS releases locks on exit."""
from contextlib import contextmanager
from pathlib import Path
import os
import time


@contextmanager
def database_gate(path, timeout=15):
    path = Path(path).resolve()
    lock_path = path.with_name(path.name + '.access.lock')
    # Never delete this file: deleting an acquired lock can create two locks.
    with lock_path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        acquired = False
        while not acquired:
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('数据库正在使用或维护，请稍后重试；恢复前请关闭网站运行窗口。')
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
