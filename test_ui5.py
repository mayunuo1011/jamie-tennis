from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,time
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import patch

import ops_auth as auth
import ops_store as store
import ops_credits as credits
import ops_reviews as reviews
import ops_service as svc
import ops_ui_service as api
import ops_maintenance as maintenance
import user_service as rules
from ops_migrate5 import migrate
import test_credits as credit_suite
import test_ops as base
from test_ops2 import NOTE,GOAL


class UI5ServiceTests(unittest.TestCase):
    def setUp(self):credit_suite.CreditTests.setUp(self);migrate()
    tearDown=base.OpsTests.tearDown
    buy=credit_suite.CreditTests.buy
    remaining=credit_suite.CreditTests.remaining
    submit=credit_suite.CreditTests.submit
    approve=credit_suite.CreditTests.approve

    def test_01_undo_and_reapprove_multiple_cycles(self):
        self.buy();sid=self.submit();self.approve(sid)
        self.assertEqual(self.remaining(),9)
        for _ in range(3):
            result=api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)
            self.assertTrue(result['refunded']);self.assertEqual(self.remaining(),10)
            self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['review_state'],'pending')
            sid=result['pending_id'];self.approve(sid);self.assertEqual(self.remaining(),9)
        with store.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM credit_ledger WHERE action='lesson' AND active=1").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT SUM(delta) FROM credit_ledger").fetchone()[0],9)

    def test_02_repeated_or_stale_undo_never_double_refunds(self):
        self.buy();sid=self.submit();self.approve(sid)
        pending=api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)['pending_id']
        with self.assertRaises(ValueError):api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)
        self.approve(pending)
        with self.assertRaises(ValueError):api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)
        self.assertEqual(self.remaining(),9)

    def test_03_manual_refund_then_undo_does_not_refund_twice(self):
        self.buy();sid=self.submit();self.approve(sid)
        credits.return_credit(self.tokens['jamie'],self.lids['luo'],'虚拟纠错')
        result=api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)
        self.assertFalse(result['refunded']);self.assertEqual(self.remaining(),10)
        self.approve(result['pending_id']);self.assertEqual(self.remaining(),9)

    def test_04_coach_cannot_undo_or_read_review_overview(self):
        self.buy();sid=self.submit();self.approve(sid)
        for action in [lambda:api.undo_completion(self.tokens['luo'],self.lids['luo'],sid),lambda:api.review_courses(self.tokens['luo'],rules.week_start()),lambda:api.current_submission(self.tokens['luo'],self.lids['luo']),lambda:api.student_summary(self.tokens['luo'],self.student)]:
            with self.assertRaises(auth.PermissionDenied):action()

    def test_05_undo_audit_failure_rolls_back_everything(self):
        self.buy();sid=self.submit();self.approve(sid);original=auth.audit
        def fail(con,actor,action,*args,**kwargs):
            if action=='undo_completion':raise RuntimeError('simulated audit failure')
            return original(con,actor,action,*args,**kwargs)
        with patch('ops_auth.audit',side_effect=fail):
            with self.assertRaises(RuntimeError):api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)
        self.assertEqual(self.remaining(),9)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['review_state'],'approved')

    def test_06_concurrent_undo_only_refunds_once(self):
        self.buy();sid=self.submit();self.approve(sid)
        def undo(_):
            try:api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(undo,[1,2])),1)
        self.assertEqual(self.remaining(),10)

    def test_07_review_overview_excludes_future_cancelled_and_moved(self):
        day=(rules.week_start()+timedelta(days=1)).isoformat()
        now=datetime.fromisoformat(day+'T16:00').replace(tzinfo=rules.LONDON)
        with store.connect() as con:
            con.execute("UPDATE lessons SET starts_at=?,ends_at=? WHERE id=?",(day+'T17:00',day+'T18:00',self.lids['jamie']))
            con.execute("UPDATE lessons SET status='已取消' WHERE id=?",(self.lids['other'],))
        with patch('user_service.now_london',return_value=now):
            self.assertEqual([r['id'] for r in api.review_courses(self.tokens['jamie'],rules.week_start())],[self.lids['luo']])

    def test_08_group_reminder_has_no_finance_or_private_coach_assignment(self):
        day=rules.week_start().isoformat();before=len(credits.history(self.tokens['jamie']))
        gid=api.save_group(self.tokens['jamie'],'虚拟小白5班',day+'T10:00',day+'T11:00','虚拟球场',[self.secret])
        self.assertIn('虚拟小白5班',api.student_summary(self.tokens['jamie'],self.secret)['classes'])
        self.assertEqual(len(credits.history(self.tokens['jamie'])),before)
        with self.assertRaises(auth.PermissionDenied):svc.student_detail(self.tokens['luo'],self.secret)
        self.assertEqual(api.groups(self.tokens['luo']),[])
        with self.assertRaises(auth.PermissionDenied):api.group_detail(self.tokens['luo'],gid)
        with self.assertRaises(auth.PermissionDenied):api.save_group(self.tokens['luo'],'班',day+'T10:00',day+'T11:00','场地')
        self.assertFalse(any(r.get('kind')=='团课' for r in api.review_courses(self.tokens['jamie'],rules.week_start())))

    def test_09_group_edit_remove_and_stale_revision(self):
        day=rules.week_start().isoformat();gid=api.save_group(self.tokens['jamie'],'虚拟班',day+'T10:00',day+'T11:00','场地')
        api.save_group(self.tokens['jamie'],'虚拟班改期',day+'T12:00',day+'T13:00','新场地',gid=gid,expected_revision=0)
        with self.assertRaises(ValueError):api.archive_group(self.tokens['jamie'],gid,0)
        api.archive_group(self.tokens['jamie'],gid,1);self.assertEqual(api.groups(self.tokens['jamie']),[])

    def test_10_migration_is_idempotent_and_v5_backup_validates(self):
        self.assertIsNone(migrate());self.buy()
        saved=maintenance.create_backup();self.assertEqual(maintenance.validate_database(saved)['schema'],5)

    def test_25_voided_purchase_does_not_remain_as_student_type(self):
        pid=self.buy(kind='陪打');credits.void_package(self.tokens['jamie'],pid,'虚拟误录')
        result=api.student_summary(self.tokens['jamie'],self.student)
        self.assertNotIn('陪打',result['classes']);self.assertEqual(result['balance'],'未录入')

    def test_26_version5_restore_preserves_cycles_and_can_reapprove(self):
        self.buy();sid=self.submit();self.approve(sid)
        pending=api.undo_completion(self.tokens['jamie'],self.lids['luo'],sid)['pending_id']
        saved=maintenance.create_backup();self.approve(pending);self.assertEqual(self.remaining(),9)
        import socket
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        maintenance.restore_backup(saved,confirmed=True,port=port);self.assertEqual(self.remaining(),10)
        self.tokens['jamie']=auth.login('jamie',base.PASSWORD)
        self.approve(pending);self.assertEqual(self.remaining(),9)

    def test_27_database_constraints_reject_second_active_charge(self):
        self.buy();sid=self.submit();self.approve(sid)
        with self.assertRaises(sqlite3.IntegrityError):
            with store.connect() as con:
                con.execute("""INSERT INTO credit_ledger(student_id,coach_id,kind,package_id,lesson_id,action,delta,actor_id,reason,created_at,active)
                  SELECT student_id,coach_id,kind,package_id,lesson_id,action,delta,actor_id,reason,created_at,1 FROM credit_ledger WHERE action='lesson' LIMIT 1""")
        self.assertEqual(self.remaining(),9)


class UI5PageTests(unittest.TestCase):
    setUp=UI5ServiceTests.setUp
    tearDown=base.OpsTests.tearDown
    buy=credit_suite.CreditTests.buy
    remaining=credit_suite.CreditTests.remaining
    submit=credit_suite.CreditTests.submit
    approve=credit_suite.CreditTests.approve

    def app(self,user='jamie',welcome=False):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')),default_timeout=30)
        app.session_state['ops_session']=self.tokens[user]
        if not welcome:app.session_state['ops_ui_welcome_seen']=True
        app.run();self.assertFalse(app.exception,[e.message for e in app.exception]);return app

    def test_11_sidebar_navigation_and_logout_at_bottom(self):
        app=self.app()
        self.assertIn('打卡审核',app.sidebar.radio[0].options)
        self.assertNotIn('课程管理',app.sidebar.radio[0].options)
        self.assertEqual(app.sidebar.radio[0].options[-1],'账号安全')
        self.assertTrue(any(b.label=='退出登录' for b in app.sidebar.button))

    def test_12_week_filter_has_exactly_three_states_and_teacher_scope(self):
        app=self.app();self.assertEqual(app.selectbox(key='ops_ui_week_status').options,['全部','没上','未打卡','已完成'])
        table=next(d for d in app.dataframe if '学员ID' in d.value.columns)
        self.assertEqual(set(table.value['教练']),{'jamie'})
        app.selectbox(key='ops_ui_coach_filter').set_value(self.coaches['luo']).run()
        table=next(d for d in app.dataframe if '学员ID' in d.value.columns)
        self.assertEqual(set(table.value['教练']),{'luo'})

    def test_13_student_table_has_balances_and_no_private_placeholder(self):
        self.buy();app=self.app();app.sidebar.radio[0].set_value('学员档案').run()
        table=next(d for d in app.dataframe if '课程余量' in d.value.columns)
        self.assertIn('所在班级 / 类型',table.value.columns)
        self.assertNotIn('水平',table.value.columns)
        self.assertNotIn('教练私有',str(table.value))

    def test_14_review_table_only_ended_and_no_attendance_column(self):
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):
            app=self.app();app.sidebar.radio[0].set_value('打卡审核').run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        table=next(d for d in app.dataframe if '打卡' in d.value.columns)
        self.assertNotIn('上课状态',table.value.columns)

    def test_15_popup_dismiss_and_todo_persists(self):
        self.submit();app=self.app(welcome=True)
        self.assertTrue(any(b.label=='知道了' for b in app.button))
        app.button(key='ops_ui_welcome_close').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(app.session_state['ops_ui_welcome_seen'])
        app.run()
        self.assertFalse(any(b.label=='知道了' for b in app.button))
        self.assertTrue(any('待审核：1' in m.value for m in app.markdown))

    def test_16_popup_and_todo_go_to_review(self):
        self.submit();app=self.app(welcome=True)
        app.button(key='ops_ui_welcome_review').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(app.sidebar.radio[0].value,'打卡审核')

    def test_17_coach_has_no_review_or_credit_pages(self):
        app=self.app('luo')
        for page in ('打卡审核','课包管理'):self.assertNotIn(page,app.sidebar.radio[0].options)

    def test_18_purchase_dialog_buy_then_confirm_void(self):
        from streamlit.testing.v1 import AppTest
        script="""import streamlit as st
from ops_ui_credits import purchase_dialog
purchase_dialog(st.session_state.token,st.session_state.sid)
"""
        app=AppTest.from_string(script,default_timeout=30);app.session_state.token=self.tokens['jamie'];app.session_state.sid=self.student;app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        app.selectbox(key=f'ops_ui_purchase_coach_{self.student}').set_value(self.coaches['luo'])
        next(b for b in app.button if b.label=='录入本次购课').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertEqual(self.remaining(),10)
        self.assertTrue(any('确定撤销这次录入吗' in a.value for a in app.warning))
        pid=credits.packages(self.tokens['jamie'])[0]['id']
        app.button(key=f'ops_ui_void_{pid}').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertEqual(self.remaining(),0)

    def test_19_review_dialog_approve_then_undo(self):
        from streamlit.testing.v1 import AppTest
        self.buy();self.submit()
        script="""import streamlit as st
from ops_ui_dialogs import review_dialog
review_dialog(st.session_state.token,st.session_state.lid)
"""
        app=AppTest.from_string(script,default_timeout=30);app.session_state.token=self.tokens['jamie'];app.session_state.lid=self.lids['luo'];app.run()
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):next(b for b in app.button if b.label=='通过审核并扣1节').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertEqual(self.remaining(),9)
        next(b for b in app.button if b.label=='确定撤销完成').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertEqual(self.remaining(),10)

    def test_22_clicking_student_cell_opens_purchase_dialog(self):
        from streamlit.testing.v1 import AppTest
        event=SimpleNamespace(selection=SimpleNamespace(cells=[(0,'称呼')],rows=[]))
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')),default_timeout=30)
        app.session_state['ops_session']=self.tokens['jamie'];app.session_state['ops_ui_welcome_seen']=True
        app.session_state['ops_navigation']='课包管理'
        with patch('streamlit.dataframe',return_value=event):app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(b.label=='录入本次购课' for b in app.button))

    def test_23_review_dialog_blocks_new_feedback_snapshot_until_reloaded(self):
        from streamlit.testing.v1 import AppTest
        self.buy();self.submit()
        script="""import streamlit as st
from ops_ui_dialogs import review_dialog
review_dialog(st.session_state.token,st.session_state.lid)
"""
        app=AppTest.from_string(script,default_timeout=30);app.session_state.token=self.tokens['jamie'];app.session_state.lid=self.lids['luo'];app.run()
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        svc.check_in(self.tokens['luo'],row['id'],NOTE+'新内容',GOAL,row['revision'])
        app.run()
        self.assertTrue(any('反馈已更新' in w.value for w in app.warning))
        self.assertEqual(self.remaining(),10)

    def test_24_week_actions_target_selected_week_and_do_not_expose_foreign_write(self):
        app=self.app()
        app.selectbox(key='ops_ui_coach_filter').set_value(self.coaches['luo']).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(any(b.label=='确认取消' for b in app.button))


class UI5MigrationTests(unittest.TestCase):
    setUp=credit_suite.CreditTests.setUp
    tearDown=base.OpsTests.tearDown
    buy=credit_suite.CreditTests.buy
    remaining=credit_suite.CreditTests.remaining
    submit=credit_suite.CreditTests.submit
    approve=credit_suite.CreditTests.approve

    def test_20_previous_refund_is_mapped_without_balance_change(self):
        self.buy();sid=self.submit();self.approve(sid);credits.return_credit(self.tokens['jamie'],self.lids['luo'],'旧版退还')
        saved=migrate();self.assertTrue(Path(saved).is_file());self.assertEqual(self.remaining(),10)
        with store.connect() as con:
            self.assertEqual(con.execute("SELECT active FROM credit_ledger WHERE action='lesson'").fetchone()[0],0)
            self.assertIsNotNone(con.execute("SELECT reverses_id FROM credit_ledger WHERE action='return'").fetchone()[0])

    def test_21_failed_migration_preserves_version4_and_backup(self):
        original=Path.read_text
        def broken(path,*args,**kwargs):return original(path,*args,**kwargs)+';INVALID SQL' if path.name=='ops_schema5.sql' else original(path,*args,**kwargs)
        with patch.object(Path,'read_text',broken):
            with self.assertRaises(sqlite3.Error):migrate()
        with store.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='group_reminders'").fetchone())
            self.assertNotIn('active',{r[1] for r in con.execute('PRAGMA table_info(credit_ledger)')})
        self.assertTrue(list(store.DB_PATH.parent.glob('backups/*.db')))
