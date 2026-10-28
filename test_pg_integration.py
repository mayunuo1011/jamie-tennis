"""Real PostgreSQL integration, opt-in via JAMIE_TEST_PG_URL (fictional only)."""
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import os,uuid,time,unittest
from unittest.mock import patch
import ops_store as store
import ops_auth as auth
import ops_service as svc
import ops_booking as booking
import ops_credits as credits
import ops_reviews as reviews
import ops_ui_service as ui
import pg_store
import cloud_migrate
import test_calendar as calendar_fixture
import test_ops as base
import test_ux_refresh as ux_fixture


class PgFixture:
    def setUp(self):
        self.previous_url=os.environ.pop('JAMIE_DATABASE_URL',None)
        self.previous_schema=os.environ.get('JAMIE_DB_SCHEMA')
        calendar_fixture.CalendarTests.setUp(self)
        self.source=store.DB_PATH
        self.schema='jt_test_'+uuid.uuid4().hex
        os.environ['JAMIE_DATABASE_URL']=os.environ['JAMIE_TEST_PG_URL']
        os.environ['JAMIE_DB_SCHEMA']=self.schema
        cloud_migrate.apply(self.source)
        with store.connect() as con:
            for name,token in self.tokens.items():con.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',(auth._token_hash(token),self.uids[name],self.now,self.now+28800,self.now))
            con.execute('INSERT INTO student_sessions VALUES(?,?,?,?,?)',(auth._token_hash(self.student_token),self.student,self.now,self.now+28800,self.now))

    def tearDown(self):
        from psycopg import sql
        try:
            if not self.schema.startswith('jt_test_') or len(self.schema)!=40:raise RuntimeError('Refusing test cleanup outside unique fixture schema')
            with pg_store.raw_connection() as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        finally:
            os.environ.pop('JAMIE_DATABASE_URL',None)
            if self.previous_url:os.environ['JAMIE_DATABASE_URL']=self.previous_url
            if self.previous_schema:os.environ['JAMIE_DB_SCHEMA']=self.previous_schema
            else:os.environ.pop('JAMIE_DB_SCHEMA',None)
            base.OpsTests.tearDown(self)


@unittest.skipUnless(os.environ.get('JAMIE_TEST_PG_URL'),'No opt-in PostgreSQL test connection')
class PgCalendarTests(PgFixture,calendar_fixture.CalendarTests):
    # Replace the SQLite-file restore test with cloud export + empty-schema restore.
    def test_19_backup_restore_revokes_alias_sessions(self):
        import cloud_backup,tempfile
        from psycopg import sql
        with tempfile.TemporaryDirectory(prefix='cloud-backup-test-') as folder:
            path=Path(folder)/'backup.json';cloud_backup.export(path)
            new_schema='jt_test_'+uuid.uuid4().hex
            os.environ['JAMIE_DB_SCHEMA']=new_schema
            try:
                result=cloud_backup.restore(path);self.assertEqual(result['status'],'imported')
                with self.assertRaises(auth.AuthenticationError):svc.list_students(self.tokens['jamie'])
                import student_auth
                self.assertEqual(student_auth.current(student_auth.login(self.alias))['student_id'],self.student)
                self.assertEqual(cloud_backup.restore(path)['status'],'already_imported')
            finally:
                with pg_store.raw_connection() as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(new_schema)))
                os.environ['JAMIE_DB_SCHEMA']=self.schema


@unittest.skipUnless(os.environ.get('JAMIE_TEST_PG_URL'),'No opt-in PostgreSQL test connection')
class PgUxTests(PgFixture,ux_fixture.RefreshTests):
    # PRAGMA checks in SQLite-only fixture tests are intentionally not present here.
    pass


@unittest.skipUnless(os.environ.get('JAMIE_TEST_PG_URL'),'No opt-in PostgreSQL test connection')
class PgMigrationTests(PgFixture,unittest.TestCase):
    account=calendar_fixture.CalendarTests.account

    def test_idempotent_same_snapshot_refuses_changed_snapshot(self):
        self.assertEqual(cloud_migrate.apply(self.source)['status'],'already_imported')
        os.environ.pop('JAMIE_DATABASE_URL')
        with store.connect() as con:con.execute("UPDATE students SET name='虚拟更名' WHERE id=?",(self.student,))
        os.environ['JAMIE_DATABASE_URL']=os.environ['JAMIE_TEST_PG_URL']
        with self.assertRaises(ValueError):cloud_migrate.apply(self.source)

    def test_shared_password_hashes_work_and_sessions_were_not_copied(self):
        token=auth.login('jamie',base.PASSWORD)
        self.assertEqual(auth.current_user(token)['role'],'operator')
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM cloud_imports').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT count(*) FROM student_sessions').fetchone()[0],1)

    def test_identity_sequence_advanced_and_no_private_profile_leak(self):
        sid=svc.save_student(self.tokens['luo'],'虚拟PG新增')
        self.assertGreater(sid,max(self.student,self.secret))
        p=svc.student_detail(self.tokens['jamie'],sid,self.coaches['luo'])
        self.assertNotIn('pain_points',p)

    def test_authentication_and_sql_parameters(self):
        with self.assertRaises(auth.AuthenticationError):svc.list_students('invalid')
        self.assertEqual(svc.list_students(self.tokens['jamie'],"' OR 1=1 --"),[])
        with store.connect() as con:
            row=con.execute("SELECT ? AS literal, ? AS zero, ? AS empty",("x?%'y",0,'')).fetchone()
            self.assertEqual((row[0],row['zero'],row['empty']),("x?%'y",0,''))


@unittest.skipUnless(os.environ.get('JAMIE_TEST_PG_URL'),'No opt-in PostgreSQL test connection')
class PgCriticalTests(PgFixture,calendar_fixture.CalendarTests):
    # Keep this class focused: tests are defined explicitly below by overriding discovery.
    def test_concurrent_booking_cannot_overspend_last_credit(self):
        self.buy(1);slots=[self.slot(10),self.slot(12)]
        def reserve(slot):
            try:self.book(slot);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(reserve,slots)),1)
        self.assertEqual(self.availability(),0);self.assertEqual(self.remaining(),1)

    def test_approval_retries_and_undo_refund_exactly_once(self):
        from datetime import datetime
        from test_ops2 import NOTE,GOAL
        self.buy(2);lid=self.book(self.slot())
        at=datetime.fromisoformat(self.day+'T23:00').replace(tzinfo=__import__('user_service').LONDON)
        with patch('user_service.now_london',return_value=at):
            svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
            sid=next(r['id'] for r in reviews.list_submissions(self.tokens['jamie']) if r['lesson_id']==lid)
            reviews.decide(self.tokens['jamie'],sid,'approved')
            with self.assertRaises(ValueError):reviews.decide(self.tokens['jamie'],sid,'approved')
            self.assertEqual(self.remaining(),1)
            result=ui.undo_completion(self.tokens['jamie'],lid,sid)
            self.assertTrue(result['refunded']);self.assertEqual(self.remaining(),2)
            with self.assertRaises(ValueError):ui.undo_completion(self.tokens['jamie'],lid,sid)
            reviews.decide(self.tokens['jamie'],result['pending_id'],'approved');self.assertEqual(self.remaining(),1)

    def test_absence_debt_and_coach_kind_separation(self):
        self.buy(1);self.cancel(self.lesson());self.cancel(self.lesson(hour=12));self.cancel(self.lesson(hour=14))
        self.assertEqual(self.remaining(),-1)
        with store.connect() as con:
            self.assertEqual(credits.balance(con,self.student,self.coaches['luo'],'陪打'),0)
            self.assertEqual(credits.balance(con,self.student,self.coaches['jamie'],'私教'),0)
        self.buy(1);self.assertEqual(self.availability(),0)
        self.buy(1);self.assertEqual(self.availability(),1)

    def test_runtime_role_can_use_app_but_cannot_change_schema(self):
        import cloud_runtime,tempfile,tomllib
        from psycopg import sql
        original=os.environ['JAMIE_DATABASE_URL']
        with tempfile.TemporaryDirectory(prefix='runtime-role-test-') as folder:
            file=Path(folder)/'secrets.toml'
            cloud_runtime.provision(file)
            config=tomllib.loads(file.read_text(encoding='utf-8'))
            try:
                os.environ['JAMIE_DATABASE_URL']=config['JAMIE_DATABASE_URL']
                self.assertEqual(auth.current_user(self.tokens['jamie'])['role'],'operator')
                self.buy(1);self.assertEqual(self.remaining(),1)
                with self.assertRaises(RuntimeError):
                    with store.connect() as con:con.execute('CREATE TABLE not_allowed(id INTEGER)')
            finally:
                os.environ['JAMIE_DATABASE_URL']=original
                with pg_store.raw_connection() as raw:
                    raw.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(cloud_runtime.ROLE)))
                    raw.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(cloud_runtime.ROLE)))


# Avoid re-running inherited calendar cases in the focused critical suite.
for _name in list(calendar_fixture.CalendarTests.__dict__):
    if _name.startswith('test_'):setattr(PgCriticalTests,_name,None)


@unittest.skipUnless(os.environ.get('JAMIE_TEST_PG_URL'),'No opt-in PostgreSQL test connection')
class PgCloudEntryTests(PgFixture,unittest.TestCase):
    account=calendar_fixture.CalendarTests.account

    def app(self,portal):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('cloud_app.py')))
        if portal:app.query_params['portal']=portal
        return app

    def test_route_landing_and_staff_login(self):
        app=self.app(None).run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any('学员预约' in m.value for m in app.markdown))
        app=self.app('staff').run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any(t.label=='登录账号' for t in app.text_input))

    def test_student_route_keeps_staff_credentials_out(self):
        app=self.app('student').run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual([t.label for t in app.text_input],['你的账号'])
        self.assertFalse(app.sidebar.radio)

    def test_authenticated_staff_cloud_workbench(self):
        app=self.app('staff')
        app.session_state['ops_session']=self.tokens['jamie']
        app.session_state['ops_ui_welcome_seen']=True
        app.run();self.assertEqual(len(app.exception),0)
        self.assertIn('课包管理',app.sidebar.radio[0].options)
