from datetime import date,datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets
import sqlite3
import unittest
from unittest.mock import patch

import ops_store as store
import ops_auth as auth
import ops_booking as api
import ops_service as svc
import ops_credits as credits
import ops_reviews as reviews
import student_auth
import user_service as rules
import booking_calendar
import ops_maintenance as maintenance
from ops_migrate7 import migrate
import test_booking as previous
import test_ops as base
from test_ops2 import NOTE,GOAL


class CalendarTests(unittest.TestCase):
    def setUp(self):
        previous.BookingTests.setUp(self);migrate()
        self.alias=student_auth.provision(self.tokens['jamie'],self.student)['username']
        self.student_token=student_auth.login(self.alias)
    tearDown=base.OpsTests.tearDown
    account=previous.BookingTests.account
    buy=previous.BookingTests.buy
    remaining=previous.BookingTests.remaining
    lesson=previous.BookingTests.lesson
    cancel=previous.BookingTests.cancel
    availability=previous.BookingTests.availability

    def slot(self,hour=10,coach='luo',kind='私教',published=True):
        slot=previous.BookingTests.slot(self,hour,coach,kind)
        if published:api.publish_week(self.tokens['jamie'],rules.week_start(date.fromisoformat(self.day)))
        return slot

    def book(self,slot,key=None,token=None):
        with store.connect() as con:revision=con.execute('SELECT revision FROM booking_slots WHERE id=?',(slot,)).fetchone()[0]
        return api.book(token or self.student_token,slot,revision,key or secrets.token_hex(16))

    def week(self):return rules.week_start(date.fromisoformat(self.day))

    def test_01_passwordless_pinyin_and_stable_alias(self):
        with store.connect() as con:con.execute("UPDATE students SET name='法棍' WHERE id=?",(self.secret,))
        alias=student_auth.provision(self.tokens['jamie'],self.secret)['username']
        self.assertRegex(alias,r'^fagun[0-9]{3}$')
        token=student_auth.login(alias)
        self.assertEqual(student_auth.current(token)['student_id'],self.secret)
        with store.connect() as con:con.execute("UPDATE students SET name='新昵称' WHERE id=?",(self.secret,))
        self.assertEqual(student_auth.provision(self.tokens['jamie'],self.secret)['username'],alias)
        with self.assertRaises(auth.AuthenticationError):student_auth.login(f'S{self.secret:03d}')

    def test_02_collision_retries_uniqueness(self):
        with store.connect() as con:con.execute('UPDATE students SET name=(SELECT name FROM students WHERE id=?) WHERE id=?',(self.student,self.secret))
        with patch('student_auth.secrets.randbelow',return_value=int(self.alias[-3:])):
            other=student_auth.provision(self.tokens['jamie'],self.secret)['username']
        self.assertNotEqual(other,self.alias);self.assertEqual(other[:-3],self.alias[:-3])

    def test_03_rotation_revokes_old_alias_and_sessions(self):
        new=student_auth.provision(self.tokens['jamie'],self.student,True)['username']
        self.assertNotEqual(new,self.alias)
        with self.assertRaises(auth.AuthenticationError):student_auth.login(self.alias)
        with self.assertRaises(auth.AuthenticationError):student_auth.current(self.student_token)
        self.assertEqual(student_auth.current(student_auth.login(new))['student_id'],self.student)

    def test_04_disable_and_staff_access_boundary(self):
        with self.assertRaises(auth.PermissionDenied):student_auth.provision(self.tokens['luo'],self.secret)
        student_auth.set_active(self.tokens['jamie'],self.student,False)
        with self.assertRaises(auth.AuthenticationError):student_auth.login(self.alias)
        with self.assertRaises(auth.AuthenticationError):svc.list_lessons(self.student_token)
        with self.assertRaises(auth.AuthenticationError):api.my_lessons(self.tokens['jamie'])

    def test_05_draft_is_hidden_and_direct_booking_rejected(self):
        self.buy(2);slot=self.slot(published=False)
        self.assertEqual(api.calendar_slots(self.student_token,self.week()),[])
        self.assertEqual(api.open_slots(self.student_token),[])
        with self.assertRaises(ValueError):self.book(slot)

    def test_06_jamie_publishes_both_coaches_but_luo_cannot_publish(self):
        self.slot(published=False);self.slot(12,'jamie',published=False)
        with self.assertRaises(auth.PermissionDenied):api.publish_week(self.tokens['luo'],self.week())
        self.assertEqual(api.publish_week(self.tokens['jamie'],self.week()),2)
        self.assertEqual(len(api.calendar_slots(self.student_token,self.week())),2)
        self.assertEqual(api.publish_week(self.tokens['jamie'],self.week()),0)

    def test_07_publication_conflict_rolls_back_whole_week(self):
        one=self.slot(published=False);self.slot(12,published=False);self.lesson(hour=12)
        with self.assertRaises(ValueError):api.publish_week(self.tokens['jamie'],self.week())
        with store.connect() as con:self.assertEqual(con.execute('SELECT published FROM booking_slots WHERE id=?',(one,)).fetchone()[0],0)

    def test_08_booked_grey_calendar_never_reveals_other_student(self):
        self.buy(2);slot=self.slot();self.book(slot)
        alias=student_auth.provision(self.tokens['jamie'],self.secret)['username'];token=student_auth.login(alias)
        row=api.calendar_slots(token,self.week())[0]
        self.assertEqual(row['state'],'已约')
        self.assertFalse(set(row)&{'student_id','student_name','contact','login_name'})
        self.assertEqual(api.calendar_slots(self.student_token,self.week())[0]['state'],'我的预约')

    def test_09_self_cancel_releases_hold_reopens_slot_no_absence(self):
        self.buy(1);slot=self.slot();lid=self.book(slot)
        self.assertTrue(api.my_lessons(self.student_token)[0]['can_cancel'])
        api.cancel_booking(self.student_token,lid,0)
        self.assertEqual((self.remaining(),self.availability()),(1,1))
        self.assertEqual(api.calendar_slots(self.student_token,self.week())[0]['state'],'可预约')
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],lid)['absence_count'],0)

    def test_10_exact_24_hours_allowed_but_one_second_inside_denied(self):
        self.buy(2);one=self.book(self.slot());two=self.book(self.slot(12))
        start=datetime.fromisoformat(self.day+'T10:00').replace(tzinfo=rules.LONDON)
        at=start.astimezone(timezone.utc)-timedelta(hours=24)
        with patch('user_service.now_london',return_value=at):api.cancel_booking(self.student_token,one,0)
        start2=datetime.fromisoformat(self.day+'T12:00').replace(tzinfo=rules.LONDON)
        at2=start2.astimezone(timezone.utc)-timedelta(hours=24)+timedelta(seconds=1)
        with patch('user_service.now_london',return_value=at2):
            with self.assertRaisesRegex(ValueError,'不足24小时'):api.cancel_booking(self.student_token,two,0)
        self.assertEqual(self.availability(),1)

    def test_11_dst_boundary_uses_elapsed_24_hours(self):
        row={'starts_at':'2026-10-25T09:00'}
        self.assertTrue(api.can_cancel(row,datetime(2026,10,24,9,tzinfo=timezone.utc)))
        self.assertFalse(api.can_cancel(row,datetime(2026,10,24,9,0,1,tzinfo=timezone.utc)))
        spring={'starts_at':'2027-03-28T09:00'}
        self.assertTrue(api.can_cancel(spring,datetime(2027,3,27,8,tzinfo=timezone.utc)))

    def test_12_other_student_cannot_cancel_and_retry_no_double_release(self):
        self.buy(1);lid=self.book(self.slot())
        alias=student_auth.provision(self.tokens['jamie'],self.secret)['username'];token=student_auth.login(alias)
        with self.assertRaises(auth.PermissionDenied):api.cancel_booking(token,lid,0)
        api.cancel_booking(self.student_token,lid,0)
        with self.assertRaises(ValueError):api.cancel_booking(self.student_token,lid,0)
        self.assertEqual(self.availability(),1)

    def test_13_cancel_audit_failure_rolls_back(self):
        self.buy(1);lid=self.book(self.slot())
        with patch('ops_auth.audit',side_effect=RuntimeError('simulated')):
            with self.assertRaises(RuntimeError):api.cancel_booking(self.student_token,lid,0)
        self.assertEqual(self.availability(),0)

    def test_14_time_passing_never_confirms_attendance_or_deducts(self):
        self.buy(1);lid=self.book(self.slot())
        at=datetime.fromisoformat(self.day+'T23:00').replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=at):
            row=svc.lesson_detail(self.tokens['luo'],lid)
            self.assertEqual(rules.attendance(row),'待确认上课')
            self.assertEqual(row['status'],'待上课');self.assertFalse(row['attendance_confirmed'])
            self.assertEqual(row['absence_count'],0)
        self.assertEqual((self.remaining(),self.availability()),(1,0))

    def test_15_teacher_confirms_attendance_without_completion(self):
        self.buy(1);lid=self.book(self.slot())
        with self.assertRaises(ValueError):svc.confirm_attendance(self.tokens['luo'],lid,0)
        at=datetime.fromisoformat(self.day+'T10:10').replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=at):svc.confirm_attendance(self.tokens['luo'],lid,0)
        row=svc.lesson_detail(self.tokens['luo'],lid)
        self.assertEqual(rules.attendance(row),'上了');self.assertEqual(row['status'],'待上课')
        self.assertEqual(self.remaining(),1)
        with self.assertRaises(auth.PermissionDenied):svc.confirm_attendance(self.tokens['jamie'],lid,1)

    def test_16_feedback_and_approval_still_deduct_once(self):
        self.buy(1);lid=self.book(self.slot())
        with self.assertRaises(ValueError):svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        at=datetime.fromisoformat(self.day+'T23:00').replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=at):
            svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
            sid=next(r['id'] for r in reviews.list_submissions(self.tokens['jamie']) if r['lesson_id']==lid)
            reviews.decide(self.tokens['jamie'],sid,'approved')
        self.assertEqual(self.remaining(),0)
        with self.assertRaises(ValueError):api.cancel_booking(self.student_token,lid,2)

    def test_17_late_cancellation_by_teacher_still_automatic_absence_debit(self):
        self.buy(2);self.cancel(self.lesson())
        lid=self.book(self.slot(12));self.cancel(lid)
        self.assertEqual(self.remaining(),1)
        self.assertEqual(api.my_lessons(self.student_token)[0]['status'],'已取消')

    def test_18_calendar_grid_has_seven_days_and_actual_start_times(self):
        self.buy(1);self.slot()
        rows=api.calendar_slots(self.student_token,self.week())
        frame,lookup=booking_calendar.frame(rows,self.week())
        self.assertEqual(len(frame.columns),8)
        self.assertIn('10:00',list(frame['时间']))
        self.assertEqual(sum(len(items) for items in lookup.values()),1)

    def test_19_backup_restore_revokes_alias_sessions(self):
        from test_maintenance import free_port
        saved=maintenance.create_backup();self.assertEqual(maintenance.validate_database(saved)['schema'],7)
        maintenance.restore_backup(saved,confirmed=True,port=free_port(),student_port=free_port())
        with self.assertRaises(auth.AuthenticationError):student_auth.current(self.student_token)
        self.assertEqual(student_auth.current(student_auth.login(self.alias))['student_id'],self.student)

    def test_20_concurrent_cancel_and_rebook_preserves_balance(self):
        self.buy(1);slot=self.slot();lid=self.book(slot)
        def cancel():
            try:api.cancel_booking(self.student_token,lid,0);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:cancel(),[0,1]))
        self.assertEqual(sum(results),1);self.assertEqual(self.availability(),1)
        self.book(slot);self.assertEqual(self.availability(),0)


class CalendarPageTests(unittest.TestCase):
    setUp=CalendarTests.setUp
    tearDown=base.OpsTests.tearDown
    account=previous.BookingTests.account
    buy=CalendarTests.buy
    slot=CalendarTests.slot
    book=CalendarTests.book
    week=CalendarTests.week

    def app(self,staff=False):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py' if staff else 'student_app.py')))
        app.session_state['ops_session' if staff else 'student_session']=self.tokens['jamie'] if staff else self.student_token
        app.run();return app

    def test_21_student_only_two_navigation_and_no_password(self):
        app=self.app()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(next(r.options for r in app.radio if r.key=='student_navigation'),['课程预约','我的预约'])
        self.assertFalse(any('密码' in item.label for item in app.text_input))
        self.assertEqual(len(app.dataframe),1)

    def test_22_login_screen_no_password_field(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('student_app.py')));app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual([i.label for i in app.text_input],['你的账号'])
        app.text_input[0].set_value(self.alias);next(b for b in app.button if b.label=='进入').click();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any(r.key=='student_navigation' for r in app.radio))

    def test_23_booking_dialog_requires_confirmation(self):
        self.buy(1);self.slot()
        rows=api.calendar_slots(self.student_token,self.week())
        app=self.app();app.session_state['student_dialog']=('book',rows);app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(api.my_lessons(self.student_token),[])
        next(b for b in app.button if b.label=='确定预约').click();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(api.my_lessons(self.student_token)),1)

    def test_24_my_bookings_cancel_confirmation(self):
        self.buy(1);self.book(self.slot())
        app=self.app();next(r for r in app.radio if r.key=='student_navigation').set_value('我的预约');app.run()
        next(b for b in app.button if b.label=='取消预约').click();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(api.my_lessons(self.student_token)[0]['status'],'待上课')
        next(b for b in app.button if b.label=='确定取消').click();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(api.my_lessons(self.student_token)[0]['status'],'已取消')

    def test_25_jamie_calendar_and_no_password_controls(self):
        self.slot(published=False)
        app=self.app(True);next(r for r in app.radio if r.key=='ops_navigation').set_value('开放预约');app.run()
        self.assertEqual(len(app.exception),0)
        self.assertTrue(any(b.key and b.key.startswith('ops_grid_add_') for b in app.button))
        self.assertFalse(any('密码' in b.label for b in app.button))


class CalendarMigrationTests(unittest.TestCase):
    setUp=previous.BookingTests.setUp
    tearDown=base.OpsTests.tearDown
    account=previous.BookingTests.account
    buy=CalendarTests.buy

    def test_26_preserve_v6_slots_accounts_and_invalidate_student_sessions(self):
        self.buy(2);old=previous.BookingTests.slot(self)
        with store.connect() as con:
            before=[tuple(r) for r in con.execute('SELECT * FROM packages')]
        migrate()
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT published FROM booking_slots WHERE id=?',(old,)).fetchone()[0],1)
            self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM packages')],before)
            self.assertEqual(con.execute('SELECT count(*) FROM student_sessions').fetchone()[0],0)
            self.assertIsNone(con.execute('SELECT login_name FROM student_accounts WHERE student_id=?',(self.student,)).fetchone()[0])
        self.assertEqual(auth.current_user(self.tokens['luo'])['role'],'coach')
        self.assertIsNone(migrate())

    def test_27_failed_migration_rolls_back_everything(self):
        from test_maintenance import digest
        before=digest(store.DB_PATH);read=Path.read_text
        def fail(path,*args,**kwargs):
            result=read(path,*args,**kwargs)
            return result+'; INVALID SQL;' if path.name=='ops_schema7.sql' else result
        with patch.object(Path,'read_text',fail):
            with self.assertRaises(sqlite3.Error):migrate()
        self.assertEqual(digest(store.DB_PATH),before)
