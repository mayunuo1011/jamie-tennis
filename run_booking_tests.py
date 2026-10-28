"""Run tests only in a temporary source copy, excluding all existing databases."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest


def main():
    source=Path(__file__).resolve().parent
    original=Path.cwd()
    os.environ.pop('COACH_PROJECT_DIR',None)
    for name in ('JAMIE_DATABASE_URL','JAMIE_DB_SCHEMA','JAMIE_TEST_PG_URL'):os.environ.pop(name,None)
    with tempfile.TemporaryDirectory(prefix='jamie-booking-safe-tests-') as temp:
        project=Path(temp)/'project';project.mkdir()
        for path in source.iterdir():
            if path.is_file() and (path.suffix in ('.py','.sql') or path.name in ('cloud_schema.json','supabase-ca.crt')):shutil.copy2(path,project/path.name)
        os.environ['JAMIE_DB_PATH']=str(Path(temp)/'guard-ops.db')
        os.environ['TENNIS_USER_DB']=str(Path(temp)/'guard-user.db')
        sys.dont_write_bytecode=True
        sys.path.insert(0,str(project))
        os.chdir(project)
        # Application imports and AppTest script paths use the isolated tree.
        try:
            suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[1]) if len(sys.argv)>1 else unittest.defaultTestLoader.discover(str(project),pattern='test_*.py')
            return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
        finally:os.chdir(original)


if __name__=='__main__':raise SystemExit(main())
