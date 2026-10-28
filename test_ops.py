import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import ops_store as store
import ops_auth as auth
import ops_service as svc
from user_service import week_start

PASSWORD='Test-only passphrase 2026!'
ENCODED=None


class OpsTests(unittest.TestCase):
    def setUp(self):
        global ENCODED
        if ENCODED is None:ENCODED=auth.password_hash(PASSWORD)
        self.old=store.DB_PATH
        self.temp=tempfile.TemporaryDirectory(prefix='jamie-phase1-test-')
        store.configure(Path(self.temp.name)/'test.db')
        from ops_migrate3 import migrate
        migrate()
        self.now=int(time.time())
        self.tokens={}
        with store.connect() as con:
            for username,role in [('admin','system_admin'),('luo','coach'),('jamie','operator'),('other','coach')]:
                uid=auth._insert_account(con,username,username,ENCODED,role)
                token='test-session-'+username+'x'*32
                con.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',(auth._token_hash(token),uid,self.now,self.now+28800,self.now))
                self.tokens[username]=token
            self.coaches={r['username']:r['coach_id'] for r in con.execute('SELECT * FROM accounts')}
            self.uids={r['username']:r['id'] for r in con.execute('SELECT * FROM accounts')}
            self.student=con.execute("INSERT INTO students(name,source,contact) VALUES('虚拟学员','小红书','虚拟联系方式')").lastrowid
            self.secret=con.execute("INSERT INTO students(name) VALUES('另一位虚拟学员')").lastrowid
            self.lids={}
            day=(week_start()+timedelta(days=1)).isoformat()
            for username in ('luo','jamie','other'):
                self.lids[username]=con.execute('''INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location,note,next_goal)
                    VALUES(?,?,?,?,?,?,?,?)''',(self.student,self.coaches[username],'私教',day+'T14:00',day+'T15:00','虚拟球场',username+' private training','目标')).lastrowid
            self.legacy=con.execute('''INSERT INTO lessons(student_id,kind,starts_at,ends_at,location)
                VALUES(?,'陪打',?,?,'虚拟球场')''',(self.student,day+'T17:00',day+'T18:00')).lastrowid

    def tearDown(self):
        store.configure(self.old);self.temp.cleanup()

    def test_01_no_session_denied(self):
        for token in (None,'','fake-session-'+'x'*32):
            with self.subTest(token=token):
                with self.assertRaises(auth.AuthenticationError):svc.list_lessons(token)
                with self.assertRaises(auth.AuthenticationError):svc.list_students(token)

    def test_02_coach_only_own_lessons_and_no_credit_fields(self):
        rows=svc.list_lessons(self.tokens['luo'])
        self.assertEqual([r['id'] for r in rows],[self.lids['luo']])
        self.assertNotIn('charged',rows[0]);self.assertNotIn('package_id',rows[0])
        self.assertNotIn('remaining',rows[0]);self.assertNotIn('charge_due',rows[0])

    def test_03_admin_and_operator_see_all_coaches(self):
        for name in ('admin','jamie'):
            self.assertEqual(len(svc.list_lessons(self.tokens[name])),4)

    def test_04_shared_base_profile_separate_training(self):
        a=svc.student_detail(self.tokens['luo'],self.student)
        b=svc.student_detail(self.tokens['jamie'],self.student)
        self.assertEqual(a,b);self.assertEqual(a['source'],'小红书')
        rows=svc.list_lessons(self.tokens['luo'],student_id=self.student)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['note'],'luo private training')

    def test_05_unrelated_student_id_cannot_bypass_scope(self):
        with self.assertRaises(auth.PermissionDenied):svc.student_detail(self.tokens['luo'],self.secret)
        self.assertNotIn(self.secret,[r['id'] for r in svc.list_students(self.tokens['luo'])])
        self.assertEqual(svc.list_lessons(self.tokens['luo'],student_id=self.secret),[])

    def test_06_explicit_student_link_and_idempotence(self):
        svc.link_student(self.tokens['jamie'],self.secret,self.coaches['luo'])
        svc.link_student(self.tokens['jamie'],self.secret,self.coaches['luo'])
        self.assertEqual(svc.student_detail(self.tokens['luo'],self.secret)['id'],self.secret)
        with store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM ops_audit WHERE action='link_student'").fetchone()[0],1)

    def test_07_unauthorized_admin_calls_denied(self):
        for call in [lambda:auth.list_accounts(self.tokens['luo']),lambda:svc.audit_log(self.tokens['jamie']),
            lambda:auth.create_account(self.tokens['jamie'],'bad','bad',PASSWORD,'system_admin'),
            lambda:svc.link_student(self.tokens['luo'],self.secret,self.coaches['luo']),
            lambda:svc.assign_legacy_lesson(self.tokens['jamie'],self.legacy,self.coaches['luo']),
            lambda:auth.set_active(self.tokens['luo'],self.uids['admin'],False)]:
            with self.assertRaises(auth.PermissionDenied):call()

    def test_08_role_in_database_rechecked_not_client_claimed(self):
        with store.connect() as con:con.execute("UPDATE accounts SET role='coach' WHERE username='jamie'")
        with self.assertRaises(auth.PermissionDenied):svc.admin_overview(self.tokens['jamie'])
        self.assertEqual(len(svc.list_lessons(self.tokens['jamie'])),1)

    def test_09_session_absolute_expiry(self):
        with store.connect() as con:con.execute('UPDATE auth_sessions SET expires_at=? WHERE token_hash=?',(self.now-1,auth._token_hash(self.tokens['luo'])))
        with self.assertRaises(auth.AuthenticationError):svc.list_lessons(self.tokens['luo'])

    def test_10_idle_expiry(self):
        with store.connect() as con:con.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?',(self.now-auth.IDLE_SECONDS-1,auth._token_hash(self.tokens['luo'])))
        with self.assertRaises(auth.AuthenticationError):svc.list_students(self.tokens['luo'])

    def test_11_disable_revokes_and_audits(self):
        auth.set_active(self.tokens['admin'],self.uids['luo'],False)
        with self.assertRaises(auth.AuthenticationError):svc.list_lessons(self.tokens['luo'])
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM auth_sessions WHERE account_id=?',(self.uids['luo'],)).fetchone()[0],0)
            self.assertEqual(con.execute('SELECT action FROM ops_audit ORDER BY id DESC').fetchone()[0],'set_account_active')

    def test_12_last_admin_cannot_be_disabled(self):
        with self.assertRaises(ValueError):auth.set_active(self.tokens['admin'],self.uids['admin'],False)
        self.assertEqual(auth.current_user(self.tokens['admin'])['active'],1)

    def test_13_real_login_and_hashed_session_storage(self):
        token=auth.login('LUO',PASSWORD)
        self.assertEqual(auth.current_user(token)['id'],self.uids['luo'])
        with store.connect() as con:
            self.assertIsNone(con.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',(token,)).fetchone())
            self.assertIsNotNone(con.execute('SELECT 1 FROM auth_sessions WHERE token_hash=?',(auth._token_hash(token),)).fetchone())

    def test_14_bad_login_persists_throttle_and_generic_error(self):
        with patch('ops_auth.verify_password',return_value=False):
            for index in range(5):
                with self.assertRaisesRegex(auth.AuthenticationError,'账号或密码不正确'):auth.login('luo','wrong')
            with self.assertRaisesRegex(auth.AuthenticationError,'暂时受限'):auth.login('luo',PASSWORD)
        with store.connect() as con:
            self.assertEqual(con.execute("SELECT failures FROM login_limits WHERE username='luo'").fetchone()[0],5)

    def test_15_unknown_user_generic_error(self):
        with self.assertRaisesRegex(auth.AuthenticationError,'账号或密码不正确'):auth.login('no-such-person',PASSWORD)

    def test_16_logout_invalidates_token(self):
        auth.logout(self.tokens['luo'])
        with self.assertRaises(auth.AuthenticationError):auth.current_user(self.tokens['luo'])

    def test_17_password_change_revokes_all_sessions(self):
        token=auth.login('luo',PASSWORD)
        auth.change_password(token,PASSWORD,'New test-only passphrase 2026!')
        for old in (token,self.tokens['luo']):
            with self.assertRaises(auth.AuthenticationError):auth.current_user(old)
        self.assertTrue(auth.login('luo','New test-only passphrase 2026!'))

    def test_18_password_policy_and_unique_salts(self):
        with self.assertRaises(ValueError):auth.password_hash('short')
        other=auth.password_hash(PASSWORD)
        self.assertNotEqual(ENCODED,other)
        self.assertTrue(auth.verify_password(PASSWORD,other))
        self.assertFalse(auth.verify_password('wrong',other))

    def test_19_account_creation_audited_and_no_password_leak(self):
        uid=auth.create_account(self.tokens['admin'],'new-coach','虚拟教练',PASSWORD,'coach')
        account=next(r for r in auth.list_accounts(self.tokens['admin']) if r['id']==uid)
        self.assertIsNotNone(account['coach_id']);self.assertNotIn('password_hash',account)
        logs=svc.audit_log(self.tokens['admin'])
        self.assertEqual(logs[0]['action'],'create_account')
        self.assertNotIn(PASSWORD,str(logs));self.assertNotIn(ENCODED,str(logs))

    def test_20_duplicate_username_rolls_back_coach_creation(self):
        with store.connect() as con:before=con.execute('SELECT count(*) FROM coaches').fetchone()[0]
        with self.assertRaises(ValueError):auth.create_account(self.tokens['admin'],'LUO','重复',PASSWORD,'coach')
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM coaches').fetchone()[0],before)

    def test_21_bootstrap_is_one_time(self):
        with self.assertRaises(auth.PermissionDenied):auth.bootstrap_admin('evil','坏账号',PASSWORD)

    def test_22_unassigned_remains_hidden_until_explicit_attribution(self):
        before=svc.admin_overview(self.tokens['admin'])['unassigned_lessons']
        svc.assign_legacy_lesson(self.tokens['admin'],self.legacy,self.coaches['luo'])
        self.assertEqual(len(svc.list_lessons(self.tokens['luo'])),2)
        self.assertEqual(svc.admin_overview(self.tokens['jamie'])['unassigned_lessons'],before-1)
        with self.assertRaises(ValueError):svc.assign_legacy_lesson(self.tokens['admin'],self.legacy,self.coaches['other'])

    def test_23_migration_idempotent_and_no_business_changes(self):
        with store.connect() as con:
            before={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in ('students','lessons','packages')}
        self.assertIsNone(store.migrate())
        with store.connect() as con:
            after={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in before}
        self.assertEqual(before,after)

    def test_24_audit_failure_rolls_back_business_change(self):
        with patch('ops_auth.audit',side_effect=RuntimeError('audit storage failed')):
            with self.assertRaises(RuntimeError):auth.set_active(self.tokens['admin'],self.uids['luo'],False)
        self.assertEqual(auth.current_user(self.tokens['luo'])['active'],1)

    def test_25_foreign_keys_reject_invalid_coach(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with store.connect() as con:con.execute('UPDATE lessons SET coach_id=999 WHERE id=?',(self.legacy,))

    def test_26_schema_preserves_kind_balance_separation(self):
        with store.connect() as con:
            for coach in ('luo','jamie'):
                for kind in ('私教','陪打'):
                    con.execute('INSERT INTO packages(student_id,kind,purchased_at,purchased,remaining,coach_id) VALUES(?,?,?,?,?,?)',
                        (self.student,kind,'2026-10-20',10,10,self.coaches[coach]))
            balances=[tuple(r) for r in con.execute('SELECT coach_id,kind,remaining FROM packages ORDER BY coach_id,kind')]
        self.assertEqual(len(balances),4)
        self.assertTrue(all(row[2]==10 for row in balances))
        self.assertEqual(len({(r[0],r[1]) for r in balances}),4)

    def test_27_legacy_migration_backup_and_preservation(self):
        import user_store as legacy
        old_legacy=legacy.DB_PATH
        target=Path(self.temp.name)/'old.db'
        try:
            legacy.configure(target);legacy.initialize()
            with legacy.connect() as con:
                sid=con.execute("INSERT INTO students(name,source) VALUES('虚拟历史学员','微信')").lastrowid
                pid=con.execute("INSERT INTO packages(student_id,kind,purchased_at,purchased,remaining) VALUES(?,'私教','2025-01-01',10,6)",(sid,)).lastrowid
                con.execute("INSERT INTO lessons(student_id,kind,starts_at,ends_at,location,charged,package_id) VALUES(?,'私教','2025-01-01T14:00','2025-01-01T15:00','虚拟地点',1,?)",(sid,pid))
                before={t:[dict(r) for r in con.execute('SELECT * FROM '+t)] for t in ('students','lessons','packages')}
            store.configure(target)
            backup=store.migrate()
            self.assertTrue(Path(backup).is_file())
            with store.connect() as con:
                for table,rows in before.items():
                    after=[dict(r) for r in con.execute('SELECT * FROM '+table)]
                    for original,new in zip(rows,after):
                        self.assertEqual({key:new[key] for key in original},original)
                        if table!='students':self.assertIsNone(new['coach_id'])
                self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(list(con.execute('PRAGMA foreign_key_check')),[])
            with closing(sqlite3.connect(backup)) as con:
                self.assertNotIn('coach_id',[r[1] for r in con.execute('PRAGMA table_info(lessons)')])
                self.assertEqual(con.execute('SELECT remaining FROM packages').fetchone()[0],6)
        finally:legacy.configure(old_legacy)

    def test_28_concurrent_attribution_one_wins(self):
        def assign(coach):
            try:svc.assign_legacy_lesson(self.tokens['admin'],self.legacy,coach);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(assign,[self.coaches['luo'],self.coaches['other']])),1)

    def test_35_demo_contains_only_fictional_data_and_never_overwrites(self):
        from ops_demo import create_demo
        store.configure(Path(self.temp.name)/'fictional-demo.db')
        credentials=create_demo()
        self.assertEqual(set(credentials),{'systemadmin','luo','jamie'})
        self.assertEqual(len(set(credentials.values())),3)
        token=auth.login('luo',credentials['luo'])
        rows=svc.list_lessons(token)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['student_name'],'虚拟学员 Alpha')
        self.assertNotIn('Jamie 的虚拟训练记录',str(rows))
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM packages').fetchone()[0],0)
        with self.assertRaises(ValueError):create_demo()

    def test_36_failed_migration_keeps_backup_and_rolls_back_new_schema(self):
        import user_store as legacy
        previous=legacy.DB_PATH
        target=Path(self.temp.name)/'migration-failure.db'
        try:
            legacy.configure(target);legacy.initialize()
            store.configure(target)
            with patch('pathlib.Path.read_text',return_value='CREATE TABLE test_atomic(id INTEGER); BAD SQL;'):
                with self.assertRaises(sqlite3.OperationalError):store.migrate()
            with store.connect() as con:
                self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='test_atomic'").fetchone())
                self.assertNotIn('coach_id',[r['name'] for r in con.execute('PRAGMA table_info(lessons)')])
            self.assertTrue(list((target.parent/'backups').glob('before_multirole_*.db')))
        finally:legacy.configure(previous)


class OpsPageTests(unittest.TestCase):
    setUp=OpsTests.setUp
    tearDown=OpsTests.tearDown

    def app(self, username=None):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')),default_timeout=30)
        if username:app.session_state['ops_session']=self.tokens[username]
        app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        return app

    def test_29_not_logged_in_no_business_data(self):
        app=self.app()
        self.assertTrue(any(b.label=='登录' for b in app.button))
        self.assertFalse(app.radio)
        self.assertNotIn('虚拟学员',str(app))

    def test_30_coach_dashboard_no_accounts_or_credit_controls(self):
        app=self.app('luo')
        self.assertNotIn('账号管理',app.radio[0].options)
        rendered=' '.join(m.value for m in app.markdown)
        self.assertEqual(list(app.dataframe[0].value['教练']),['luo'])
        for word in ('余额','扣课','已用课时','课包'):self.assertNotIn(word,rendered)

    def test_31_operator_all_coaches_no_account_administration(self):
        app=self.app('jamie')
        self.assertNotIn('账号管理',app.radio[0].options)
        # Operator starts on their own schedule and can explicitly switch to all.
        next(s for s in app.selectbox if s.key=='ops_coach_filter').set_value(0).run()
        self.assertIn('luo',list(app.dataframe[0].value['教练']))
        self.assertIn('other',list(app.dataframe[0].value['教练']))

    def test_32_system_admin_account_and_audit_pages(self):
        app=self.app('admin')
        for page in ('账号管理','操作记录','学员档案','账号安全'):
            app.radio[0].set_value(page).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])

    def test_33_real_login_then_logout(self):
        app=self.app()
        next(t for t in app.text_input if t.label=='登录账号').set_value('luo')
        next(t for t in app.text_input if t.label=='密码').set_value(PASSWORD)
        next(b for b in app.button if b.label=='登录').click().run()
        self.assertFalse(app.exception);self.assertTrue(app.radio)
        next(b for b in app.button if b.label=='退出登录').click().run()
        self.assertFalse(app.exception);self.assertFalse(app.radio)
        self.assertFalse(any('private training' in m.value for m in app.markdown))

    def test_34_disabled_session_redirects_to_login(self):
        app=self.app('luo')
        auth.set_active(self.tokens['admin'],self.uids['luo'],False)
        app.run()
        self.assertFalse(app.exception);self.assertFalse(app.radio)

    def test_37_admin_creates_account_through_web(self):
        app=self.app('admin')
        app.radio[0].set_value('账号管理').run()
        next(t for t in app.text_input if t.label=='新账号').set_value('new-preview-coach')
        next(t for t in app.text_input if t.label=='显示名称').set_value('虚拟教练 Z')
        next(t for t in app.text_input if t.label=='初始密码（15–128 字符）').set_value(PASSWORD)
        next(b for b in app.button if b.label=='创建账号').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(a['username']=='new-preview-coach' for a in auth.list_accounts(self.tokens['admin'])))


if __name__=='__main__':unittest.main(verbosity=2)
