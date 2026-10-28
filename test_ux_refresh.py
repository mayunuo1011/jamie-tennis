"""UX and permission regressions: fictional students, isolated temporary DBs."""
from datetime import date
from pathlib import Path
from unittest.mock import patch
import unittest
import secrets
import ops_auth as auth
import ops_store as store
import ops_service as svc
import ops_booking as booking
import ops_credits as credits
import ops_ui_service as ui
import test_calendar as fixture
import test_ops as base


class RefreshTests(unittest.TestCase):
    setUp=fixture.CalendarTests.setUp
    tearDown=base.OpsTests.tearDown
    account=fixture.CalendarTests.account
    week=fixture.CalendarTests.week

    def register(self,key=None,**kwargs):
        args=dict(token=self.tokens['jamie'],name='虚拟新学员',cid=self.coaches['luo'],kind='私教',purchased_at=date.today(),quantity=3,request_key=key or secrets.token_hex(16))
        args.update(kwargs)
        return credits.register_student_package(**args)

    def external(self,name='Zoey',hour=10,token=None):
        return booking.publish(token or self.tokens['jamie'],'私教',self.day+f'T{hour:02}:00',self.day+f'T{hour+1:02}:00','虚拟球场',external_name=name)

    def test_01_atomic_registration_and_retry(self):
        key=secrets.token_hex(16);sid=self.register(key)
        self.assertEqual(self.register(key),sid)
        with store.connect() as con:
            self.assertEqual(credits.balance(con,sid,self.coaches['luo'],'私教'),3)
            self.assertEqual(con.execute('SELECT count(*) FROM packages WHERE student_id=?',(sid,)).fetchone()[0],1)
        self.assertIn(sid,[s['id'] for s in svc.list_students(self.tokens['luo'])])
        self.assertNotIn('pain_points',svc.student_detail(self.tokens['jamie'],sid,self.coaches['luo']))

    def test_02_registration_failure_rolls_back_student_and_purchase(self):
        with store.connect() as con:before=con.execute('SELECT count(*) FROM students').fetchone()[0]
        original=auth.audit
        def fail(con,actor,action,*args,**kwargs):
            if action=='purchase_credits':raise RuntimeError('simulated')
            return original(con,actor,action,*args,**kwargs)
        with patch('ops_auth.audit',side_effect=fail):
            with self.assertRaises(RuntimeError):self.register()
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM students').fetchone()[0],before)

    def test_03_coach_cannot_register_credits(self):
        with self.assertRaises(auth.PermissionDenied):self.register(token=self.tokens['luo'])
        with self.assertRaises(ValueError):self.register(quantity=0)

    def test_04_filter_assignment_and_show_teacher_even_without_package(self):
        svc.link_student(self.tokens['jamie'],self.secret,self.coaches['luo'])
        self.assertIn(self.secret,[s['id'] for s in svc.list_students(self.tokens['jamie'],coach_id=self.coaches['luo'])])
        self.assertNotIn(self.secret,[s['id'] for s in svc.list_students(self.tokens['jamie'],coach_id=self.coaches['jamie'])])
        summary=ui.student_summary(self.tokens['jamie'],self.secret)
        self.assertEqual(summary['coaches'],'luo');self.assertEqual(summary['purchases'],'未录入')
        self.assertEqual(len(svc.list_students(self.tokens['jamie'])),2)

    def test_05_external_coaches_have_no_login_and_can_publish(self):
        for i,name in enumerate(booking.EXTERNAL_COACHES):self.external(name,hour=10+i)
        self.assertEqual(booking.publish_week(self.tokens['jamie'],self.week()),3)
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM accounts WHERE coach_id IN (SELECT id FROM coaches WHERE name IN (?,?,?))',booking.EXTERNAL_COACHES).fetchone()[0],0)
        self.assertEqual(len(booking.calendar_slots(self.student_token,self.week())),3)

    def test_06_external_booking_still_requires_assignment_and_credit(self):
        slot=self.external();booking.publish_week(self.tokens['jamie'],self.week())
        with store.connect() as con:row=dict(con.execute('SELECT * FROM booking_slots WHERE id=?',(slot,)).fetchone())
        with self.assertRaises(auth.PermissionDenied):booking.book(self.student_token,slot,row['revision'],secrets.token_hex(16))
        credits.add_package(self.tokens['jamie'],self.student,row['coach_id'],'私教',date.today(),1,secrets.token_hex(16))
        lid=booking.book(self.student_token,slot,row['revision'],secrets.token_hex(16))
        self.assertTrue(lid)
        with store.connect() as con:self.assertEqual(credits.available(con,self.student,row['coach_id'],'私教'),0)

    def test_07_no_cross_coach_edit_or_unapproved_external_name(self):
        with self.assertRaises(auth.PermissionDenied):self.external(token=self.tokens['luo'])
        with self.assertRaises(ValueError):self.external('未批准教练')
        with self.assertRaises(auth.PermissionDenied):booking.publish(self.tokens['jamie'],'私教',self.day+'T10:00',self.day+'T11:00','虚拟球场',self.coaches['luo'])
        slot=booking.publish(self.tokens['luo'],'私教',self.day+'T10:00',self.day+'T11:00','虚拟球场')
        with self.assertRaises(auth.PermissionDenied):booking.close_slot(self.tokens['jamie'],slot,0)
        self.assertIn(slot,[s['id'] for s in booking.staff_slots(self.tokens['jamie'])])

    def test_08_same_time_different_teachers_allowed_but_same_teacher_conflicts(self):
        self.external('Zoey');self.external('小刘');self.external('Zoey',12)
        with self.assertRaises(ValueError):self.external('Zoey')
        self.assertEqual(len(booking.staff_slots(self.tokens['jamie'])),3)

    def test_09_disabled_account_not_bypassed_by_external_name(self):
        slot=self.external()
        with store.connect() as con:
            cid=con.execute('SELECT coach_id FROM booking_slots WHERE id=?',(slot,)).fetchone()[0]
            con.execute("INSERT INTO accounts(username,display_name,password_hash,role,coach_id,active,created_at) VALUES('zoey-test','Zoey','unused','coach',?,0,0)",(cid,))
        with self.assertRaises(ValueError):booking.publish_week(self.tokens['jamie'],self.week())
        with self.assertRaises(auth.PermissionDenied):self.external(hour=12)

    def app(self,role='jamie',page='学员档案'):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')),default_timeout=20)
        app.session_state['ops_session']=self.tokens[role]
        app.session_state['ops_navigation']=page
        app.session_state['ops_ui_welcome_seen']=True
        app.run();self.assertEqual(len(app.exception),0)
        return app

    def test_10_navigation_and_profile_no_add(self):
        app=self.app()
        options=app.sidebar.radio[0].options
        self.assertEqual(options[:3],['本周课程','开放预约','学员档案'])
        self.assertNotIn('课表导入',options)
        self.assertFalse(any(b.label=='新增学员' for b in app.button))
        table=next(d.value for d in app.dataframe if '学员ID' in d.value.columns)
        self.assertIn('对接教练',table.columns);self.assertIn('已购课程',table.columns)
        self.assertTrue(any('缺课待处理' in e.label for e in app.sidebar.expander))

    def test_11_actual_registration_form_then_history_dialog(self):
        app=self.app(page='课包管理')
        next(t for t in app.text_input if t.label=='学员称呼 *').set_value('虚拟界面新学员')
        next(s for s in app.selectbox if s.key=='ops_register_coach').set_value(self.coaches['luo'])
        next(b for b in app.button if b.label=='添加学员并录入课时').click();app.run()
        self.assertEqual(len(app.exception),0);self.assertEqual(len(app.error),0)
        person=next(s for s in svc.list_students(self.tokens['jamie']) if s['name']=='虚拟界面新学员')
        app.session_state['ops_ui_dialog']=('credit',person['id']);app.run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any('确定撤销' in b.label for b in app.button))

    def test_12_calendar_cell_opens_form_and_saves_external_draft(self):
        app=self.app(page='开放预约')
        key=f'ops_grid_add_{self.day}_10:00'
        # Navigate to fixture week (the next week on some weekdays).
        app.session_state['ops_booking_calendar_week']=self.week();app.run()
        next(b for b in app.button if b.key==key).click();app.run()
        self.assertEqual(len(app.exception),0)
        selector=next(s for s in app.selectbox if s.label=='教练')
        self.assertIn('Zoey',selector.options)
        selector.set_value('external:Zoey')
        next(b for b in app.button if b.label=='保存草稿').click();app.run()
        self.assertEqual(len(app.exception),0);self.assertEqual(len(app.error),0)
        rows=booking.staff_slots(self.tokens['jamie']);self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['coach_name'],'Zoey');self.assertFalse(rows[0]['published'])
        self.assertEqual(booking.calendar_slots(self.student_token,self.week()),[])

    def test_13_multi_slot_grid_and_luo_only_own_choices(self):
        self.external();self.external('小刘');self.external(hour=12)
        app=self.app(page='开放预约')
        app.session_state['ops_booking_calendar_week']=self.week();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith('ops_grid_slot_')]),3)
        app=self.app('luo','开放预约')
        app.session_state['ops_booking_calendar_week']=self.week();app.run()
        next(b for b in app.button if b.key==f'ops_grid_add_{self.day}_10:00').click();app.run()
        self.assertEqual(next(s for s in app.selectbox if s.label=='教练').options,['luo'])
