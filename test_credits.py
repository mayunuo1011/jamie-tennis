from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timedelta,time
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import ops_auth as auth
import ops_store as store
import ops_service as svc
import ops_reviews as review
import ops_credits as credits
import ops_maintenance as maintenance
import user_service as rules
from ops_migrate4 import migrate
import test_ops as base
from test_ops2 import NOTE,GOAL


class CreditTests(unittest.TestCase):
    def setUp(self):
        base.OpsTests.setUp(self)
        migrate()
        self.seq=0

    tearDown=base.OpsTests.tearDown

    def buy(self,quantity=10,coach='luo',kind='私教',day='2026-10-20',key=None):
        self.seq+=1
        return credits.add_package(self.tokens['jamie'],self.student,self.coaches[coach],kind,day,quantity,key or f'purchase-test-{self.seq:03}')

    def remaining(self,coach='luo',kind='私教'):
        with store.connect() as con:return credits.balance(con,self.student,self.coaches[coach],kind)

    def submit(self,coach='luo'):
        lid=self.lids[coach]
        svc.check_in(self.tokens[coach],lid,NOTE,GOAL,svc.lesson_detail(self.tokens[coach],lid)['revision'])
        return next(r['id'] for r in review.list_submissions(self.tokens['jamie'],'pending') if r['lesson_id']==lid)

    def approve(self,sid):
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):return review.decide(self.tokens['jamie'],sid,'approved')

    def test_01_purchase_is_persistent_and_scoped(self):
        self.buy();self.buy(4,'jamie');self.buy(7,kind='陪打')
        self.assertEqual((self.remaining(),self.remaining('jamie'),self.remaining(kind='陪打')),(10,4,7))
        self.assertEqual(len(credits.history(self.tokens['jamie'],self.student)),3)

    def test_02_coach_cannot_read_or_write_credit_endpoints(self):
        calls=[lambda:credits.balances(self.tokens['luo']),lambda:credits.history(self.tokens['luo']),
               lambda:credits.packages(self.tokens['luo']),lambda:credits.pending_absences(self.tokens['luo']),
               lambda:credits.add_package(self.tokens['luo'],self.student,self.coaches['luo'],'私教','2026-10-20',10,'purchase-denied'),
               lambda:credits.void_package(self.tokens['luo'],1,'test'),lambda:credits.return_credit(self.tokens['luo'],1,'test'),
               lambda:credits.settle_absence(self.tokens['luo'],1)]
        for call in calls:
            with self.assertRaises(auth.PermissionDenied):call()

    def test_03_purchase_validation(self):
        for quantity in (0,-1,1.5,True,10001):
            with self.assertRaises(ValueError):self.buy(quantity)
        self.assertEqual(self.remaining(),0)

    def test_04_purchase_request_is_idempotent_and_detects_conflict(self):
        first=self.buy(key='same-purchase-key')
        self.assertEqual(first,self.buy(key='same-purchase-key'))
        with self.assertRaises(ValueError):self.buy(9,key='same-purchase-key')
        self.assertEqual(self.remaining(),10)

    def test_05_concurrent_purchase_creates_once(self):
        def buy(_):return credits.add_package(self.tokens['jamie'],self.student,self.coaches['luo'],'私教','2026-10-20',10,'concurrent-purchase-key')
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(buy,[1,2]))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.remaining(),10)

    def test_06_purchase_audit_failure_rolls_back(self):
        with patch('ops_auth.audit',side_effect=RuntimeError('simulated audit failure')):
            with self.assertRaises(RuntimeError):self.buy()
        self.assertEqual(credits.packages(self.tokens['jamie']),[])
        self.assertEqual(credits.history(self.tokens['jamie']),[])

    def test_07_no_balance_blocks_manual_schedule(self):
        start=(rules.week_start()+timedelta(days=3)).isoformat()
        with self.assertRaisesRegex(ValueError,'未购买'):
            svc.create_lesson(self.tokens['luo'],self.student,'私教',start+'T10:00',start+'T11:00','虚拟场地',self.coaches['luo'],'create-no-balance')
        self.buy(kind='陪打')
        with self.assertRaises(ValueError):
            svc.create_lesson(self.tokens['luo'],self.student,'私教',start+'T10:00',start+'T11:00','虚拟场地',self.coaches['luo'],'create-wrong-type')

    def test_08_purchase_allows_schedule_without_deducting(self):
        self.buy()
        start=(rules.week_start()+timedelta(days=3)).isoformat()
        svc.create_lesson(self.tokens['luo'],self.student,'私教',start+'T10:00',start+'T11:00','虚拟场地',self.coaches['luo'],'create-with-balance')
        self.assertEqual(self.remaining(),10)

    def test_09_approval_deducts_one_and_separates_other_balances(self):
        self.buy();self.buy(5,'jamie');self.buy(8,kind='陪打')
        sid=self.submit()
        self.assertEqual(self.approve(sid)['credit_action'],'charged')
        self.assertEqual((self.remaining(),self.remaining('jamie'),self.remaining(kind='陪打')),(9,5,8))
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        self.assertEqual(row['status'],'已完成')
        self.assertNotIn('remaining',row);self.assertNotIn('charged',row)

    def test_10_repeated_approval_and_credit_charge_do_not_repeat(self):
        self.buy();sid=self.submit();self.approve(sid)
        with self.assertRaises(ValueError):self.approve(sid)
        with store.connect() as con:
            user=auth.require(con,self.tokens['jamie'])
            self.assertEqual(credits.charge(con,user,self.lids['luo']),'already_charged')
        self.assertEqual(self.remaining(),9)

    def test_11_insufficient_balance_keeps_review_pending(self):
        sid=self.submit()
        with self.assertRaisesRegex(ValueError,'余额不足'):self.approve(sid)
        self.assertEqual(review.list_submissions(self.tokens['jamie'],'pending')[0]['id'],sid)
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT charged FROM lessons WHERE id=?',(self.lids['luo'],)).fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM credit_ledger').fetchone()[0],0)

    def test_12_failed_review_audit_rolls_back_charge_and_approval(self):
        self.buy();sid=self.submit();original=auth.audit
        def fail_review(con,actor,action,*args,**kwargs):
            if action=='review_feedback_approved':raise RuntimeError('simulated review failure')
            return original(con,actor,action,*args,**kwargs)
        with patch('ops_auth.audit',side_effect=fail_review):
            with self.assertRaises(RuntimeError):self.approve(sid)
        self.assertEqual(self.remaining(),10)
        self.assertEqual(review.list_submissions(self.tokens['jamie'],'pending')[0]['id'],sid)

    def test_13_fifo_uses_earliest_purchase(self):
        later=self.buy(2,day='2026-10-21');earlier=self.buy(1,day='2026-10-19')
        self.approve(self.submit())
        rows={r['id']:r for r in credits.packages(self.tokens['jamie'])}
        self.assertEqual((rows[earlier]['remaining'],rows[later]['remaining']),(0,2))

    def test_14_approved_feedback_can_change_without_state_or_balance_change(self):
        self.buy();self.approve(self.submit());lid=self.lids['luo']
        row=svc.lesson_detail(self.tokens['luo'],lid)
        svc.check_in(self.tokens['luo'],lid,NOTE+'继续练习',GOAL,row['revision'])
        row=svc.lesson_detail(self.tokens['luo'],lid)
        self.assertEqual((row['status'],row['review_state'],self.remaining()),('已完成','approved',9))
        self.assertEqual(review.inbox_stats(self.tokens['jamie'])['pending'],0)
        self.assertEqual(len(review.list_submissions(self.tokens['jamie'],history=True)),2)

    def test_15_completed_course_cannot_cancel_reschedule_or_remove_feedback(self):
        self.buy();sid=self.submit();self.approve(sid);lid=self.lids['luo'];row=svc.lesson_detail(self.tokens['luo'],lid)
        with self.assertRaises(ValueError):svc.cancel(self.tokens['luo'],lid,'天气',True,'',row['revision'])
        with self.assertRaises(ValueError):svc.reschedule(self.tokens['luo'],lid,None,time(10),time(11),'虚拟场地','',row['revision'])
        with self.assertRaises(ValueError):svc.clear_feedback(self.tokens['luo'],lid,row['revision'])
        with self.assertRaises(ValueError):review.decide(self.tokens['jamie'],sid,'returned','test')
        self.assertEqual(self.remaining(),9)

    def test_16_void_unused_package_and_prevent_double_void(self):
        pid=self.buy()
        credits.void_package(self.tokens['jamie'],pid,'虚拟误录')
        self.assertEqual(self.remaining(),0)
        with self.assertRaises(ValueError):credits.void_package(self.tokens['jamie'],pid,'again')
        self.assertEqual(len(credits.history(self.tokens['jamie'])),2)

    def test_17_used_package_cannot_be_voided(self):
        pid=self.buy();self.approve(self.submit())
        with self.assertRaises(ValueError):credits.void_package(self.tokens['jamie'],pid,'虚拟错误')

    def test_18_return_one_credit_is_audited_and_cannot_repeat(self):
        self.buy();self.approve(self.submit());lid=self.lids['luo']
        credits.return_credit(self.tokens['jamie'],lid,'虚拟测试纠错')
        self.assertEqual(self.remaining(),10)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],lid)['status'],'已完成')
        with self.assertRaises(ValueError):credits.return_credit(self.tokens['jamie'],lid,'again')
        with store.connect() as con:self.assertEqual(credits.lesson_credit_state(con,lid),'已退还1节')

    def test_19_cross_coach_second_absence_waits_for_jamie_and_uses_correct_balance(self):
        self.buy();self.buy(3,'jamie')
        svc.cancel(self.tokens['luo'],self.lids['luo'],'学员取消',False,'',0)
        self.assertEqual(credits.pending_absences(self.tokens['jamie']),[])
        svc.cancel(self.tokens['jamie'],self.lids['jamie'],'学员取消',False,'',0)
        self.assertEqual(len(credits.pending_absences(self.tokens['jamie'])),1)
        self.assertEqual(self.remaining('jamie'),3)
        credits.settle_absence(self.tokens['jamie'],self.lids['jamie'])
        self.assertEqual((self.remaining(),self.remaining('jamie')),(10,2))
        with self.assertRaises(ValueError):credits.settle_absence(self.tokens['jamie'],self.lids['jamie'])

    def test_20_absence_insufficient_balance_stays_unpaid(self):
        svc.cancel(self.tokens['luo'],self.lids['luo'],'学员取消',False,'',0)
        svc.cancel(self.tokens['jamie'],self.lids['jamie'],'学员取消',False,'',0)
        self.assertEqual(credits.settle_absence(self.tokens['jamie'],self.lids['jamie']),'unpaid')
        self.assertEqual(len(credits.pending_absences(self.tokens['jamie'])),1)
        self.buy(2,'jamie');self.assertEqual(credits.settle_absence(self.tokens['jamie'],self.lids['jamie']),'charged')

    def test_21_import_blocks_missing_balance_without_creating_empty_profiles(self):
        from ops_import import process
        day=(rules.week_start()+timedelta(days=3)).isoformat()
        rows=[dict(name='虚拟新同学',kind='私教',starts_at=day+'T10:00',ends_at=day+'T11:00',location='Meadows')]
        result=process(self.tokens['luo'],rows,self.coaches['luo'],True,template='xiaoluo')
        self.assertIn('未购买',result[0]['result'])
        with store.connect() as con:self.assertIsNone(con.execute("SELECT 1 FROM students WHERE name='虚拟新同学'").fetchone())

    def test_22_concurrent_approval_deducts_once(self):
        self.buy();sid=self.submit()
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        def approve(_):
            try:review.decide(self.tokens['jamie'],sid,'approved');return True
            except ValueError:return False
        with patch('user_service.now_london',return_value=now):
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(approve,[1,2])),1)
        self.assertEqual(self.remaining(),9)

    def test_23_version4_backup_and_restore_keep_credits_and_require_same_schema(self):
        self.buy();saved=maintenance.create_backup()
        self.assertEqual(maintenance.validate_database(saved)['schema'],4)
        with store.connect() as con:con.execute('UPDATE packages SET remaining=2')
        import socket
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        maintenance.restore_backup(saved,confirmed=True,port=port)
        self.assertEqual(self.remaining(),10)

    def test_26_one_remaining_credit_two_approvals_never_go_negative(self):
        self.buy(1)
        day=(rules.week_start()+timedelta(days=2)).isoformat()
        lid=svc.create_lesson(self.tokens['luo'],self.student,'私教',day+'T16:00',day+'T17:00','虚拟场地',self.coaches['luo'],'second-approval-lesson')
        self.submit()
        svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        ids=[r['id'] for r in review.list_submissions(self.tokens['jamie'],'pending')]
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        def approve(sid):
            try:review.decide(self.tokens['jamie'],sid,'approved');return True
            except ValueError:return False
        with patch('user_service.now_london',return_value=now):
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(approve,ids)),1)
        self.assertEqual(self.remaining(),0)
        self.assertEqual(review.inbox_stats(self.tokens['jamie'])['pending'],1)


class CreditPageTests(unittest.TestCase):
    setUp=CreditTests.setUp
    tearDown=base.OpsTests.tearDown
    buy=CreditTests.buy
    remaining=CreditTests.remaining
    submit=CreditTests.submit
    approve=CreditTests.approve
    app=base.OpsPageTests.app

    def test_27_jamie_can_purchase_from_credit_page(self):
        app=self.app('jamie')
        app.radio[0].set_value('课包管理').run()
        next(s for s in app.selectbox if s.key=='ops_credit_student').set_value(self.student).run()
        next(s for s in app.selectbox if s.key=='ops_credit_coach').set_value(self.coaches['luo'])
        next(n for n in app.number_input if n.key=='ops_credit_quantity').set_value(10)
        next(b for b in app.button if b.label=='确认录入课包').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(self.remaining(),10)

    def test_28_coach_navigation_does_not_show_credit_panel(self):
        self.buy()
        app=self.app('luo')
        self.assertNotIn('课包管理',app.radio[0].options)
        rendered=' '.join(m.value for m in app.markdown)
        self.assertNotIn('余额',rendered)

    def test_29_jamie_approves_through_ui_and_deducts(self):
        self.buy();self.submit()
        app=self.app('jamie')
        app.radio[0].set_value('课程管理').run()
        next(r for r in app.radio if r.key=='review_management_view').set_value('反馈审核').run()
        next(b for b in app.button if b.label=='查看这份反馈').click().run()
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):
            next(b for b in app.button if b.label=='确认审核完成').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(self.remaining(),9)

    def test_30_owner_feedback_form_is_editable_after_approval(self):
        self.buy();self.approve(self.submit())
        from streamlit.testing.v1 import AppTest
        script="""import streamlit as st
from ops_facade import WorkspaceService
import user_views as views
service=WorkspaceService(st.session_state.token)
views.record_form(service.lesson(st.session_state.lesson_id),'approved_edit',service)
"""
        app=AppTest.from_string(script,default_timeout=30)
        app.session_state.token=self.tokens['luo'];app.session_state.lesson_id=self.lids['luo'];app.run()
        self.assertTrue(any(t.label=='课后总结 / 训练记录' for t in app.text_area))
        next(t for t in app.text_area if t.label=='课后总结 / 训练记录').set_value(NOTE+'补充动作要点')
        next(b for b in app.button if b.label=='保存修改').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(self.remaining(),9)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['status'],'已完成')

    def test_31_system_admin_can_open_credit_panel(self):
        app=self.app('admin')
        app.radio[0].set_value('课包管理').run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(b.label=='确认录入课包' for b in app.button))


class CreditMigrationTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown

    def test_24_upgrade_preserves_existing_data_and_does_not_deduct_old_approvals(self):
        svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE,GOAL,0)
        sid=review.list_submissions(self.tokens['jamie'],'pending')[0]['id']
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):review.decide(self.tokens['jamie'],sid,'approved')
        with store.connect() as con:
            con.execute("INSERT INTO packages(student_id,coach_id,kind,purchased_at,purchased,remaining) VALUES(?,?,'私教','2026-10-20',10,6)",(self.student,self.coaches['luo']))
            before={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in ('accounts','lessons','packages','feedback_submissions')}
        saved=migrate();self.assertTrue(Path(saved).exists());self.assertIsNone(migrate())
        with store.connect() as con:
            for table,values in before.items():self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM '+table)],values)
            row=con.execute('SELECT action,delta FROM credit_ledger').fetchone()
            self.assertEqual(tuple(row),('opening',6))

    def test_25_failed_schema_rolls_back_and_keeps_backup(self):
        original=Path.read_text
        def broken(path,*args,**kwargs):
            return original(path,*args,**kwargs)+';INVALID SQL' if path.name=='ops_schema4.sql' else original(path,*args,**kwargs)
        with patch.object(Path,'read_text',broken):
            with self.assertRaises(sqlite3.Error):migrate()
        with store.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='credit_ledger'").fetchone())
            self.assertEqual({r[0] for r in con.execute('SELECT version FROM ops_migrations')},{1,2,3})
        self.assertTrue(list(store.DB_PATH.parent.glob('backups/*.db')))

    def test_32_restore_rejects_schema_downgrade(self):
        saved=migrate()
        import socket
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with patch('ops_maintenance._replace') as replace:
            with self.assertRaisesRegex(ValueError,'版本不同'):
                maintenance.restore_backup(saved,confirmed=True,port=port)
            replace.assert_not_called()
        with store.connect() as con:self.assertTrue(credits.enabled(con))
