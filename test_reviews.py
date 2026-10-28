from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timedelta,time
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_ops as base
import ops_auth as auth
import ops_service as svc
import ops_reviews as reviews
import ops_store as store
import user_service as rules
from ops_migrate3 import migrate
from test_ops2 import NOTE,GOAL


class ReviewTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown

    def submit(self,actor='luo'):
        lid=self.lids[actor]
        revision=svc.lesson_detail(self.tokens[actor],lid)['revision']
        svc.check_in(self.tokens[actor],lid,NOTE,GOAL,revision)
        return reviews.list_submissions(self.tokens['jamie'],'pending')[0]['id']

    def decide(self,sid,decision='approved',comment=''):
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):return reviews.decide(self.tokens['jamie'],sid,decision,comment)

    def test_01_submit_creates_persistent_pending_and_unread(self):
        sid=self.submit()
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],1)
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['unread'],1)
        with store.connect() as con:
            row=con.execute('SELECT * FROM feedback_submissions WHERE id=?',(sid,)).fetchone()
            self.assertEqual(row['note'],NOTE)
            self.assertEqual(row['submitted_by'],self.uids['luo'])

    def test_02_read_receipt_does_not_approve(self):
        sid=self.submit();reviews.open_submission(self.tokens['jamie'],sid)
        stats=reviews.inbox_stats(self.tokens['jamie'])
        self.assertEqual((stats['pending'],stats['unread']),(1,0))
        self.assertEqual(reviews.list_submissions(self.tokens['jamie'])[0]['state'],'pending')

    def test_03_coach_cannot_approve_even_own_feedback(self):
        sid=self.submit()
        with self.assertRaises(auth.PermissionDenied):reviews.decide(self.tokens['luo'],sid,'approved')
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],1)

    def test_04_approve_marks_complete_without_credit_change(self):
        sid=self.submit()
        with store.connect() as con:
            before=[tuple(r) for r in con.execute('SELECT id,charged,charge_due,package_id FROM lessons')]
        self.assertEqual(self.decide(sid)['credit_action'],'none')
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        self.assertEqual((row['status'],row['review_state']),('已完成','approved'))
        with store.connect() as con:
            self.assertEqual([tuple(r) for r in con.execute('SELECT id,charged,charge_due,package_id FROM lessons')],before)
            self.assertEqual(con.execute('SELECT count(*) FROM packages').fetchone()[0],0)

    def test_05_repeated_approval_cannot_create_duplicate_audit(self):
        sid=self.submit();self.decide(sid)
        with self.assertRaises(ValueError):self.decide(sid)
        with store.connect() as con:self.assertEqual(con.execute("SELECT count(*) FROM ops_audit WHERE action='review_feedback_approved'").fetchone()[0],1)

    def test_06_concurrent_approval_only_one_wins(self):
        sid=self.submit()
        def approve(_):
            try:reviews.decide(self.tokens['jamie'],sid,'approved');return True
            except ValueError:return False
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):
            with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(approve,[1,2])),1)

    def test_07_approved_feedback_is_locked_for_edits_and_deletion(self):
        sid=self.submit();self.decide(sid)
        revision=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['revision']
        with self.assertRaises(ValueError):svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE+'修改',GOAL,revision)
        with self.assertRaises(ValueError):svc.clear_feedback(self.tokens['luo'],self.lids['luo'],revision)

    def test_08_return_requires_reason(self):
        sid=self.submit()
        with self.assertRaises(ValueError):self.decide(sid,'returned','  ')
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],1)

    def test_09_return_then_edit_creates_new_snapshot_and_pending(self):
        old=self.submit();self.decide(old,'returned','请补充击球表现')
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        self.assertEqual((row['review_state'],row['review_comment']),('returned','请补充击球表现'))
        svc.check_in(self.tokens['luo'],self.lids['luo'],NOTE+'补充表现',GOAL,row['revision'])
        latest=reviews.list_submissions(self.tokens['jamie'])[0]
        self.assertNotEqual(old,latest['id']);self.assertEqual(latest['state'],'pending')
        with store.connect() as con:
            original=con.execute('SELECT note,state,is_current FROM feedback_submissions WHERE id=?',(old,)).fetchone()
            self.assertEqual(tuple(original),(NOTE,'returned',0))

    def test_10_approved_can_be_returned_then_resubmitted(self):
        sid=self.submit();self.decide(sid);self.decide(sid,'returned','需要补充细节')
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        self.assertEqual(row['status'],'待上课')
        svc.check_in(self.tokens['luo'],row['id'],NOTE+'补充细节',GOAL,row['revision'])
        self.assertEqual(reviews.list_submissions(self.tokens['jamie'])[0]['state'],'pending')

    def test_11_edit_while_reviewer_reads_makes_old_approval_invalid(self):
        old=self.submit();reviews.open_submission(self.tokens['jamie'],old)
        row=svc.lesson_detail(self.tokens['luo'],self.lids['luo'])
        svc.check_in(self.tokens['luo'],row['id'],NOTE+'新版本',GOAL,row['revision'])
        with self.assertRaises(ValueError):self.decide(old)
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],1)

    def test_12_withdrawn_feedback_cannot_be_approved(self):
        old=self.submit();svc.clear_feedback(self.tokens['luo'],self.lids['luo'],1)
        with self.assertRaises(ValueError):self.decide(old)
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],0)

    def test_13_cancellation_withdraws_review(self):
        sid=self.submit();svc.cancel(self.tokens['luo'],self.lids['luo'],'天气',True,'',1)
        with self.assertRaises(ValueError):self.decide(sid)

    def test_14_reschedule_withdraws_review(self):
        sid=self.submit();svc.reschedule(self.tokens['luo'],self.lids['luo'],None,time(10),time(11),'Meadows','',1)
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['pending'],0)
        with self.assertRaises(ValueError):self.decide(sid)

    def test_15_other_coach_cannot_read_submission_or_private_read_receipt(self):
        sid=self.submit()
        with self.assertRaises(auth.PermissionDenied):reviews.open_submission(self.tokens['other'],sid)
        self.assertEqual(reviews.list_submissions(self.tokens['other']),[])

    def test_16_weekly_overview_is_operations_only_and_shows_separate_states(self):
        sid=self.submit();self.decide(sid)
        with self.assertRaises(auth.PermissionDenied):reviews.weekly_overview(self.tokens['luo'],rules.week_start())
        rows=reviews.weekly_overview(self.tokens['jamie'],rules.week_start())
        row=next(r for r in rows if r['教练']=='luo')
        self.assertEqual(row['打卡'],'已打卡');self.assertEqual(row['确认完成'],'已确认完成')
        self.assertIn('未扣课',row['扣课'])

    def test_17_review_does_not_grant_scheduling_rights(self):
        sid=self.submit();self.decide(sid)
        revision=svc.lesson_detail(self.tokens['jamie'],self.lids['luo'])['revision']
        with self.assertRaises(auth.PermissionDenied):svc.cancel(self.tokens['jamie'],self.lids['luo'],'天气',True,'',revision)

    def test_18_no_approval_before_course_ends(self):
        day=rules.week_start()+timedelta(days=14)
        lid=svc.create_lesson(self.tokens['luo'],self.student,'私教',f'{day}T10:00',f'{day}T11:00','Meadows',None,'future-review-request-001')
        svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        sid=next(r['id'] for r in reviews.list_submissions(self.tokens['jamie']) if r['lesson_id']==lid)
        with self.assertRaises(ValueError):self.decide(sid)

    def test_19_failed_audit_rolls_back_approval_and_completion(self):
        sid=self.submit()
        with patch('ops_auth.audit',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.decide(sid)
        self.assertEqual(reviews.list_submissions(self.tokens['jamie'])[0]['state'],'pending')
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],self.lids['luo'])['status'],'待上课')

    def test_20_migration_backfills_valid_feedback_without_approving_or_deducting(self):
        from ops_migrate2 import migrate as migrate2
        target=Path(self.temp.name)/'stage2-with-feedback.db'
        store.configure(target);migrate2()
        auth.bootstrap_admin('systemadmin','虚拟管理员',base.PASSWORD)
        token=auth.login('systemadmin',base.PASSWORD)
        uid=auth.create_account(token,'luo','Luo',base.PASSWORD,'coach')
        with store.connect() as con:
            cid=con.execute('SELECT coach_id FROM accounts WHERE id=?',(uid,)).fetchone()[0]
            sid=con.execute("INSERT INTO students(name) VALUES('虚拟历史学员')").lastrowid
            for note in (NOTE,'短笔记'):
                con.execute("INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location,note,next_goal,checkin_done) VALUES(?,?,'私教','2026-01-01T10:00','2026-01-01T11:00','Meadows',?,?,1)",(sid,cid,note,GOAL))
            encoded=con.execute('SELECT password_hash FROM accounts WHERE username=?',('systemadmin',)).fetchone()[0]
        saved=migrate()
        self.assertEqual(reviews.inbox_stats(token)['pending'],1)
        row=reviews.list_submissions(token)[0]
        self.assertEqual((row['source'],row['state'],row['submitted_by']),('migration','pending',None))
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT SUM(charged) FROM lessons').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM lessons').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT password_hash FROM accounts WHERE username=?',('systemadmin',)).fetchone()[0],encoded)
        with closing(sqlite3.connect(saved)) as con:self.assertEqual(con.execute('SELECT version FROM ops_migrations ORDER BY version').fetchall(),[(1,),(2,)])
        self.assertIsNone(migrate())

    def test_21_failed_migration_rolls_back_schema_and_keeps_backup(self):
        from ops_migrate2 import migrate as migrate2
        target=Path(self.temp.name)/'failed-review.db'
        store.configure(target);migrate2()
        with patch('pathlib.Path.read_text',return_value='CREATE TABLE review_test_rollback(id INTEGER); BAD SQL;'):
            with self.assertRaises(sqlite3.OperationalError):migrate()
        with store.connect() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='review_test_rollback'").fetchone())
            self.assertEqual([r[0] for r in con.execute('SELECT version FROM ops_migrations ORDER BY version')],[1,2])
        self.assertTrue(list((target.parent/'backups').glob('*.db')))


class ReviewPageTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown
    app=base.OpsPageTests.app
    submit=ReviewTests.submit
    decide=ReviewTests.decide

    def open_panel(self):
        app=self.app('jamie')
        app.button(key='review_open_inbox').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        return app

    def test_22_submission_reminder_opens_review_center(self):
        self.submit();app=self.open_panel()
        self.assertEqual(app.radio(key='ops_navigation').value,'课程管理')
        self.assertEqual(app.radio(key='review_management_view').value,'反馈审核')
        self.assertTrue(any('待审核 1' in x.value for x in app.info))

    def test_23_read_then_approve_through_ui(self):
        sid=self.submit();app=self.open_panel()
        app.button(key=f'review_read_{sid}').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(NOTE==x.value for x in app.markdown))
        now=datetime.combine(rules.week_start()+timedelta(days=6),time(23)).replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=now):
            next(b for b in app.button if b.label=='确认审核完成').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(reviews.inbox_stats(self.tokens['jamie'])['approved'],1)
        self.assertTrue(any('未扣课' in x.value for x in app.success))

    def test_24_return_reason_and_coach_notice(self):
        sid=self.submit();app=self.open_panel()
        app.button(key=f'review_read_{sid}').click().run()
        app.text_area(key=f'review_comment_{sid}').set_value('请补充训练表现')
        next(b for b in app.button if b.label=='退回修改').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        coach=self.app('luo')
        self.assertTrue(any('被退回' in x.value for x in coach.warning))

    def test_25_operator_management_three_views_and_no_foreign_write_forms(self):
        self.submit();app=self.app('jamie')
        app.radio(key='ops_navigation').set_value('课程管理').run()
        self.assertTrue(any(h.value=='运营课程管理' for h in app.subheader))
        self.assertIn('扣课',app.dataframe[0].value.columns)
        self.assertFalse(any(b.label=='确认取消' for b in app.button))
        app.radio(key='review_management_view').set_value('我自己的排课').run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(b.label=='确认取消' for b in app.button))
        self.assertEqual(app.selectbox(key='user_cancel_selector').value,self.lids['jamie'])

    def test_26_approved_feedback_dialog_has_no_edit_or_delete(self):
        from types import SimpleNamespace
        sid=self.submit();self.decide(sid)
        event=SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        with patch('streamlit.dataframe',return_value=event):
            app=self.app('luo')
            self.assertFalse(app.exception)
            self.assertFalse(any(t.label=='课后总结 / 训练记录' for t in app.text_area))
            self.assertFalse(any(b.label=='删除反馈' for b in app.button))


if __name__=='__main__':unittest.main(verbosity=2)
