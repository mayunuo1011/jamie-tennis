from datetime import timedelta,time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_ops as base
import ops_service as svc
import ops_auth as auth
import ops_import as importer
import ops_store as store
import user_service as rules
from test_ops2 import NOTE,GOAL


class OwnershipTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown

    def test_01_jamie_can_read_but_not_cancel_reschedule_archive_luo(self):
        lid=self.lids['luo']
        self.assertEqual(svc.lesson_detail(self.tokens['jamie'],lid)['coach_id'],self.coaches['luo'])
        for call in [lambda:svc.cancel(self.tokens['jamie'],lid,'天气',True,'',0),
            lambda:svc.reschedule(self.tokens['jamie'],lid,None,time(10),time(11),'Meadows','',0),
            lambda:svc.archive_lesson(self.tokens['jamie'],lid,0)]:
            with self.assertRaises(auth.PermissionDenied):call()
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],lid)['revision'],0)

    def test_02_jamie_cannot_create_or_import_luo_courses(self):
        day=rules.week_start().isoformat()
        with self.assertRaises(auth.PermissionDenied):svc.create_lesson(self.tokens['jamie'],self.student,'私教',day+'T10:00',day+'T11:00','Meadows',self.coaches['luo'],'denied-request-key-001')
        row=dict(name='虚拟学生',kind='私教',starts_at=day+'T10:00',ends_at=day+'T11:00',location='Meadows')
        with self.assertRaises(auth.PermissionDenied):importer.process(self.tokens['jamie'],[row],self.coaches['luo'],True)

    def test_03_jamie_can_create_cancel_and_move_own_courses(self):
        day=rules.week_start().isoformat()
        lid=svc.create_lesson(self.tokens['jamie'],self.student,'私教',day+'T10:00',day+'T11:00','Meadows',self.coaches['jamie'],'own-request-key-001')
        svc.cancel(self.tokens['jamie'],lid,'天气',True,'',0)
        moved=svc.reschedule(self.tokens['jamie'],self.lids['jamie'],rules.week_start()+timedelta(days=7),time(10),time(11),'Grange Club','',0)
        self.assertEqual(svc.lesson_detail(self.tokens['jamie'],moved)['coach_id'],self.coaches['jamie'])

    def test_04_feedback_owner_can_clear_and_resubmit_without_finance(self):
        lid=self.lids['luo']
        svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        svc.clear_feedback(self.tokens['luo'],lid,1)
        row=svc.lesson_detail(self.tokens['luo'],lid)
        self.assertEqual((row['note'],row['next_goal'],row['checkin_done'],row['checked_in_at']),('','',0,None))
        svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,2)
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT charged FROM lessons WHERE id=?',(lid,)).fetchone()[0],0)
            self.assertEqual(con.execute("SELECT count(*) FROM ops_audit WHERE action='clear_own_feedback'").fetchone()[0],1)
            self.assertIn(NOTE,con.execute("SELECT details FROM ops_audit WHERE action='clear_own_feedback'").fetchone()[0])

    def test_05_jamie_cannot_clear_or_rewrite_luo_feedback(self):
        lid=self.lids['luo'];svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        with self.assertRaises(auth.PermissionDenied):svc.clear_feedback(self.tokens['jamie'],lid,1)
        with self.assertRaises(auth.PermissionDenied):svc.check_in(self.tokens['jamie'],lid,NOTE+'修改',GOAL,1)

    def test_06_stale_feedback_delete_and_settlement_marker_rejected(self):
        lid=self.lids['luo'];svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        with self.assertRaises(svc.StaleRecord):svc.clear_feedback(self.tokens['luo'],lid,0)
        with store.connect() as con:con.execute('UPDATE lessons SET charged=1 WHERE id=?',(lid,))
        with self.assertRaises(ValueError):svc.clear_feedback(self.tokens['luo'],lid,1)

    def test_07_system_admin_correction_is_audited(self):
        svc.cancel(self.tokens['admin'],self.lids['luo'],'天气',True,'本机管理更正',0)
        with store.connect() as con:
            row=con.execute("SELECT actor_id FROM ops_audit WHERE action='cancel_lesson'").fetchone()
            self.assertEqual(row['actor_id'],self.uids['admin'])


class OwnershipPageTests(unittest.TestCase):
    setUp=base.OpsTests.setUp
    tearDown=base.OpsTests.tearDown
    app=base.OpsPageTests.app

    def test_08_jamie_foreign_schedule_and_management_have_no_write_forms(self):
        app=self.app('jamie')
        app.selectbox(key='ops_coach_filter').set_value(self.coaches['luo']).run()
        self.assertFalse(any(b.label=='添加课程' for b in app.button))
        app.radio(key='ops_navigation').set_value('课程管理').run()
        self.assertFalse(app.exception)
        labels=[b.label for b in app.button]
        for denied in ('确认取消','保存改期','移除课程'):self.assertNotIn(denied,labels)

    def test_09_owner_deletes_feedback_in_dialog(self):
        lid=self.lids['luo'];svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        event=SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        with patch('streamlit.dataframe',return_value=event):
            app=self.app('luo')
            app.checkbox(key=f'clear_feedback_confirm_{lid}').check().run()
            app.button(key=f'clear_feedback_submit_{lid}').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],lid)['checkin_done'],0)


if __name__=='__main__':unittest.main(verbosity=2)
