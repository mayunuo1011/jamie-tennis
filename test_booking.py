from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,time
from pathlib import Path
import secrets
import sqlite3
import unittest
from unittest.mock import patch

import ops_auth as auth
import ops_store as store
import ops_credits as credits
import ops_service as svc
import ops_booking as booking
import ops_ui_service as ui_api
import ops_reviews as reviews
import ops_maintenance as maintenance
import ops_import as importer
import student_auth
import user_service as rules
from ops_migrate6 import migrate
import test_ops as base
import test_credits as credit_tests
from test_ops2 import NOTE,GOAL


class BookingTests(unittest.TestCase):
    def setUp(self):
        credit_tests.CreditTests.setUp(self);migrate()
        with store.connect() as con:con.execute('UPDATE lessons SET archived=1')
        self.day=(rules.today_london()+timedelta(days=3)).isoformat()
        self.student_token=self.account(self.student)
    tearDown=base.OpsTests.tearDown
    buy=credit_tests.CreditTests.buy
    remaining=credit_tests.CreditTests.remaining

    def account(self,sid):
        token=secrets.token_urlsafe(32)
        with store.connect() as con:
            con.execute('INSERT INTO student_accounts(student_id,password_hash,must_change) VALUES(?,?,0)',(sid,base.ENCODED))
            con.execute('INSERT INTO student_sessions VALUES(?,?,?,?,?)',(auth._token_hash(token),sid,self.now,self.now+28800,self.now))
        return token

    def slot(self,hour=10,coach='luo',kind='私教'):
        return booking.publish(self.tokens[coach],kind,self.day+f'T{hour:02}:00',self.day+f'T{hour+1:02}:00','虚拟球场',self.coaches[coach])

    def book(self,slot,key=None,token=None):
        return booking.book(token or self.student_token,slot,0,key or secrets.token_hex(16))

    def lesson(self,coach='luo',kind='私教',hour=10):
        with store.connect() as con:
            return con.execute('INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?,?)',
              (self.student,self.coaches[coach],kind,self.day+f'T{hour:02}:00',self.day+f'T{hour+1:02}:00','虚拟球场')).lastrowid

    def cancel(self,lid,coach='luo',reason='学员取消',advance=False):
        row=svc.lesson_detail(self.tokens[coach],lid)
        svc.cancel(self.tokens[coach],lid,reason,advance,'虚拟取消',row['revision'])

    def availability(self):
        with store.connect() as con:return credits.available(con,self.student,self.coaches['luo'],'私教')

    def test_01_booking_reserves_without_charging(self):
        self.buy(2);slot=self.slot();lid=self.book(slot)
        self.assertEqual((self.remaining(),self.availability()),(2,1))
        self.assertEqual(booking.my_credits(self.student_token)[0]['occupied'],1)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],lid)['student_id'],self.student)
        self.assertFalse(any(r['action']=='lesson' for r in credits.history(self.tokens['jamie'])))

    def test_02_one_credit_cannot_reserve_two_lessons(self):
        self.buy(1);one=self.slot();two=self.slot(12);self.book(one)
        with self.assertRaises(ValueError):self.book(two)
        with self.assertRaises(ValueError):svc.create_lesson(self.tokens['luo'],self.student,'私教',self.day+'T14:00',self.day+'T15:00','场地',request_key='manual-create-limited')

    def test_03_concurrent_students_only_one_gets_slot(self):
        self.buy(2);other=self.account(self.secret)
        credits.add_package(self.tokens['jamie'],self.secret,self.coaches['luo'],'私教',self.day,2,'second-student-purchase')
        slot=self.slot()
        def attempt(token):
            try:return self.book(slot,token=token)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[self.student_token,other]))
        self.assertEqual(sum(r is not None for r in results),1)

    def test_04_concurrent_bookings_cannot_overspend_reservation(self):
        self.buy(1);slots=[self.slot(),self.slot(12)]
        def attempt(slot):
            try:return self.book(slot)
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,slots))
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(self.availability(),0)

    def test_05_request_retry_is_idempotent(self):
        self.buy(2);slot=self.slot();lid=self.book(slot,'same-request-key')
        self.assertEqual(self.book(slot,'same-request-key'),lid)
        with self.assertRaises(ValueError):self.book(self.slot(12),'same-request-key')

    def test_06_cancel_releases_and_reopens_slot(self):
        self.buy(1);slot=self.slot();lid=self.book(slot);self.cancel(lid,advance=True)
        self.assertEqual(self.availability(),1)
        self.assertIn(slot,[r['id'] for r in booking.open_slots(self.student_token)])

    def test_07_first_absence_free_second_automatic_negative(self):
        first=self.lesson();self.cancel(first)
        self.assertEqual(self.remaining(),0)
        second=self.lesson(hour=12);self.cancel(second)
        self.assertEqual(self.remaining(),-1)
        self.assertEqual(credits.pending_absences(self.tokens['jamie']),[])
        with self.assertRaises(ValueError):self.cancel(second)
        self.assertEqual(self.remaining(),-1)
        self.buy(1);self.assertEqual(self.remaining(),0)
        with self.assertRaises(ValueError):self.book(self.slot(14))
        self.buy(1);self.assertEqual(self.remaining(),1)
        self.book(next(r['id'] for r in booking.open_slots(self.student_token) if r['starts_at'].endswith('14:00')))

    def test_08_absences_cross_coaches_charge_correct_scope(self):
        self.buy(2);self.buy(2,kind='陪打');self.buy(2,coach='jamie')
        self.cancel(self.lesson())
        self.cancel(self.lesson('jamie',hour=12),'jamie')
        self.assertEqual((self.remaining(),self.remaining('jamie'),self.remaining(kind='陪打')),(2,1,2))
        self.cancel(self.lesson(kind='陪打',hour=14))
        self.assertEqual(self.remaining(kind='陪打'),1)

    def test_09_weather_coach_and_24h_never_count(self):
        for i,(reason,advance) in enumerate([('天气',False),('教练原因',False),('学员取消',True),('其他',False)]):
            self.cancel(self.lesson(hour=10+i),reason=reason,advance=advance)
        self.assertEqual(svc.student_detail(self.tokens['jamie'],self.student)['absence_count'],0)
        self.assertEqual(self.remaining(),0)

    def test_10_absence_failure_rolls_back_record_and_debit(self):
        self.cancel(self.lesson());second=self.lesson(hour=12)
        original=auth.audit
        def fail(con,actor,action,*args,**kwargs):
            if action=='cancel_lesson':raise RuntimeError('simulated audit failure')
            return original(con,actor,action,*args,**kwargs)
        with patch('ops_auth.audit',side_effect=fail):
            with self.assertRaises(RuntimeError):self.cancel(second)
        self.assertEqual(self.remaining(),0)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],second)['status'],'待上课')

    def test_11_approval_converts_hold_and_undo_restores_hold(self):
        self.buy(2);lid=self.book(self.slot())
        svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        sub=ui_api.current_submission(self.tokens['jamie'],lid)['id']
        future=datetime.fromisoformat(self.day+'T23:00').replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=future):reviews.decide(self.tokens['jamie'],sub,'approved')
        self.assertEqual((self.remaining(),self.availability()),(1,1))
        pending=ui_api.undo_completion(self.tokens['jamie'],lid,sub)
        self.assertEqual((self.remaining(),self.availability()),(2,1))
        with patch('user_service.now_london',return_value=future):reviews.decide(self.tokens['jamie'],pending['pending_id'],'approved')
        self.assertEqual((self.remaining(),self.availability()),(1,1))

    def test_12_reschedule_moves_hold_without_extra_credit(self):
        self.buy(1);lid=self.book(self.slot())
        new=svc.reschedule(self.tokens['luo'],lid,self.day,time(12),time(13),'新场地','',0)
        self.assertEqual(self.availability(),0)
        self.assertEqual(booking.my_credits(self.student_token)[0]['occupied'],1)
        self.assertEqual(svc.lesson_detail(self.tokens['luo'],new)['location'],'新场地')

    def test_13_pending_reschedule_retains_hold(self):
        self.buy(1);lid=self.book(self.slot())
        svc.reschedule(self.tokens['luo'],lid,None,time(12),time(13),'场地','',0)
        self.assertEqual(self.availability(),0)
        svc.reschedule(self.tokens['luo'],lid,self.day,time(14),time(15),'场地','',1)
        self.assertEqual(booking.my_credits(self.student_token)[0]['occupied'],1)

    def test_14_zero_other_coach_or_kind_not_spendable(self):
        self.buy(2,coach='jamie');self.buy(2,kind='陪打')
        with self.assertRaises(ValueError):self.book(self.slot())

    def test_15_student_and_staff_sessions_cannot_cross(self):
        with self.assertRaises(auth.AuthenticationError):booking.my_credits(self.tokens['jamie'])
        with self.assertRaises(auth.AuthenticationError):svc.list_students(self.student_token)
        with self.assertRaises(auth.AuthenticationError):booking.publish(self.student_token,'私教',self.day+'T10:00',self.day+'T11:00','场地',self.coaches['luo'])

    def test_16_publication_ownership_and_stale_or_closed_slot(self):
        with self.assertRaises(auth.PermissionDenied):booking.publish(self.tokens['jamie'],'私教',self.day+'T10:00',self.day+'T11:00','场地',self.coaches['luo'])
        slot=self.slot()
        with self.assertRaises(auth.PermissionDenied):booking.close_slot(self.tokens['jamie'],slot,0)
        booking.close_slot(self.tokens['luo'],slot,0)
        with self.assertRaises(ValueError):self.book(slot)

    def test_17_slot_conflicts_and_group_conflicts(self):
        self.slot()
        with self.assertRaises(ValueError):self.slot()
        ui_api.save_group(self.tokens['jamie'],'虚拟团课',self.day+'T12:00',self.day+'T13:00','场地')
        with self.assertRaises(ValueError):self.slot(12,'jamie')
        with self.assertRaises(ValueError):booking.publish(self.tokens['luo'],'团课',self.day+'T14:00',self.day+'T15:00','场地',self.coaches['luo'])

    def test_18_student_schedule_private_and_overlap(self):
        self.buy(2);self.buy(2,coach='jamie');self.book(self.slot())
        with self.assertRaises(ValueError):self.book(self.slot(coach='jamie'))
        other=self.account(self.secret)
        self.assertEqual(booking.my_lessons(other),[])
        fields=set(booking.my_lessons(self.student_token)[0])
        self.assertFalse(fields & {'note','next_goal','contact','pain_points','student_name'})

    def test_19_void_cannot_remove_reserved_credit(self):
        pid=self.buy(1);self.book(self.slot())
        with self.assertRaises(ValueError):credits.void_package(self.tokens['jamie'],pid,'误录')

    def test_20_negative_refund_once_and_no_coach_finance_access(self):
        self.cancel(self.lesson());second=self.lesson(hour=12);self.cancel(second)
        credits.return_credit(self.tokens['jamie'],second,'缺课误扣纠错');self.assertEqual(self.remaining(),0)
        with self.assertRaises(ValueError):credits.return_credit(self.tokens['jamie'],second,'重复')
        with self.assertRaises(auth.PermissionDenied):credits.return_credit(self.tokens['luo'],second,'无权限')

    def test_21_provision_login_initial_change_reset_and_disable(self):
        result=student_auth.provision(self.tokens['jamie'],self.secret)
        token=student_auth.login(result['username'],result['initial_password'])
        self.assertTrue(student_auth.current(token)['must_change'])
        with self.assertRaises(auth.PermissionDenied):booking.open_slots(token)
        student_auth.change_password(token,result['initial_password'],base.PASSWORD)
        with self.assertRaises(auth.AuthenticationError):student_auth.current(token)
        token=student_auth.login(result['username'],base.PASSWORD)
        self.assertFalse(student_auth.current(token)['must_change'])
        with self.assertRaises(ValueError):student_auth.provision(self.tokens['jamie'],self.secret)
        student_auth.provision(self.tokens['jamie'],self.secret,True)
        with self.assertRaises(auth.AuthenticationError):student_auth.current(token)
        student_auth.set_active(self.tokens['jamie'],self.secret,False)
        with self.assertRaises(auth.AuthenticationError):student_auth.login(result['username'],base.PASSWORD)

    def test_22_coach_cannot_provision_or_reset_student(self):
        with self.assertRaises(auth.PermissionDenied):student_auth.provision(self.tokens['luo'],self.secret)
        with self.assertRaises(auth.PermissionDenied):student_auth.set_active(self.tokens['luo'],self.student,False)

    def test_23_login_throttle_persists(self):
        with patch('ops_auth.verify_password',return_value=False):
            for _ in range(5):
                with self.assertRaises(auth.AuthenticationError):student_auth.login(f'S{self.student:03d}','wrong')
        with self.assertRaisesRegex(auth.AuthenticationError,'受限'):student_auth.login(f'S{self.student:03d}',base.PASSWORD)

    def test_24_backup_and_idempotent_upgrade(self):
        self.buy(2);self.book(self.slot())
        self.assertIsNone(migrate())
        saved=maintenance.create_backup()
        self.assertEqual(maintenance.validate_database(saved)['schema'],6)
        with store.connect() as con:
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_25_booking_audit_failure_rolls_back(self):
        self.buy(1);slot=self.slot()
        with patch('ops_auth.audit',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):self.book(slot)
        self.assertEqual(self.availability(),1);self.assertEqual(booking.my_lessons(self.student_token),[])

    def test_26_past_and_group_slots_rejected(self):
        with self.assertRaises(ValueError):booking.publish(self.tokens['luo'],'私教','2020-01-01T10:00','2020-01-01T11:00','场地',self.coaches['luo'])
        with self.assertRaises(ValueError):booking.publish(self.tokens['luo'],'团课',self.day+'T10:00',self.day+'T11:00','场地',self.coaches['luo'])

    def test_27_second_absence_converts_one_hold_one_debit(self):
        self.buy(2);self.cancel(self.lesson())
        slot=self.slot(12);lid=self.book(slot);self.cancel(lid)
        self.assertEqual((self.remaining(),self.availability()),(1,1))
        self.assertEqual(booking.my_credits(self.student_token)[0]['occupied'],0)

    def test_28_historical_pending_absences_not_auto_charged(self):
        lid=self.lesson()
        with store.connect() as con:
            con.execute("UPDATE lessons SET status='已取消',absence=1,charge_due=1 WHERE id=?",(lid,))
        self.assertIsNone(migrate());self.assertEqual(self.remaining(),0)
        self.assertEqual(len(credits.pending_absences(self.tokens['jamie'])),1)

    def test_29_import_preview_and_write_respect_total_available(self):
        self.buy(1)
        rows=[{'name':'虚拟学员','student_id':self.student,'kind':'私教','starts_at':self.day+f'T{h:02}:00','ends_at':self.day+f'T{h+1:02}:00','location':'虚拟球场'} for h in (10,12)]
        preview=importer.process(self.tokens['luo'],rows,self.coaches['luo'],False)
        self.assertEqual(sum(r['result']=='可以导入' for r in preview),1)
        result=importer.process(self.tokens['luo'],rows,self.coaches['luo'],True)
        self.assertEqual(sum(r['result']=='已添加' for r in result),1)

    def test_33_restore_revokes_student_sessions_and_preserves_reservations(self):
        from test_maintenance import free_port
        self.buy(1);self.book(self.slot());saved=maintenance.create_backup()
        maintenance.restore_backup(saved,confirmed=True,port=free_port(),student_port=free_port())
        with self.assertRaises(auth.AuthenticationError):student_auth.current(self.student_token)
        with self.assertRaises(auth.AuthenticationError):auth.current_user(self.tokens['luo'])
        self.assertEqual(self.availability(),0)
        self.assertEqual(len(booking.my_lessons(student_auth.login(f'S{self.student:03d}',base.PASSWORD))),1)

    def test_34_concurrent_first_two_cancellations_charge_once(self):
        first=self.lesson();second=self.lesson('jamie',hour=12)
        def cancel(args):self.cancel(*args)
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(cancel,[(first,'luo'),(second,'jamie')]))
        self.assertEqual(self.remaining()+self.remaining('jamie'),-1)
        self.assertEqual(svc.student_detail(self.tokens['jamie'],self.student)['absence_count'],2)

    def test_35_debt_blocks_normal_approval_even_with_positive_package(self):
        self.cancel(self.lesson());self.cancel(self.lesson(hour=12));self.buy(1)
        lid=self.lesson(hour=14);svc.check_in(self.tokens['luo'],lid,NOTE,GOAL,0)
        sub=ui_api.current_submission(self.tokens['jamie'],lid)['id']
        future=datetime.fromisoformat(self.day+'T23:00').replace(tzinfo=rules.LONDON)
        with patch('user_service.now_london',return_value=future):
            with self.assertRaises(ValueError):reviews.decide(self.tokens['jamie'],sub,'approved')
        self.assertEqual(self.remaining(),0)
        self.assertEqual(ui_api.current_submission(self.tokens['jamie'],lid)['state'],'pending')

    def test_36_dst_ambiguous_or_nonexistent_slot_rejected(self):
        for start,end in [('2027-03-28T01:00','2027-03-28T02:00'),('2027-10-31T01:00','2027-10-31T02:00')]:
            with self.assertRaisesRegex(ValueError,'夏令时'):booking.publish(self.tokens['luo'],'私教',start,end,'场地',self.coaches['luo'])

    def test_37_deleted_mistaken_booking_releases_hold(self):
        self.buy(1);lid=self.book(self.slot());svc.archive_lesson(self.tokens['luo'],lid,0)
        self.assertEqual(self.availability(),1)

    def test_38_closed_student_port_blocks_restore(self):
        import socket
        from test_maintenance import free_port
        saved=maintenance.create_backup()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.listen()
            with self.assertRaisesRegex(ValueError,str(port)):
                maintenance.restore_backup(saved,confirmed=True,port=free_port(),student_port=port)
        self.assertEqual(student_auth.current(self.student_token)['student_id'],self.student)


class BookingPageTests(unittest.TestCase):
    setUp=BookingTests.setUp
    tearDown=base.OpsTests.tearDown
    account=BookingTests.account
    buy=credit_tests.CreditTests.buy
    slot=BookingTests.slot

    def test_30_staff_navigation_and_publishing_page(self):
        from streamlit.testing.v1 import AppTest
        import os
        with patch.dict(os.environ,{'JAMIE_DB_PATH':str(store.DB_PATH)}):
            app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')))
            app.session_state['ops_session']=self.tokens['luo'];app.run()
            nav=next(r for r in app.radio if r.key=='ops_navigation')
            self.assertIn('开放预约',nav.options);nav.set_value('开放预约');app.run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(b.label=='发布可预约时段' for b in app.button))
            self.assertFalse(any(b.label=='创建学员登录账号' for b in app.button))

    def test_31_student_page_and_booking(self):
        from streamlit.testing.v1 import AppTest
        self.buy(1);self.slot()
        app=AppTest.from_file(str(Path(__file__).with_name('student_app.py')))
        app.session_state['student_session']=self.student_token;app.run()
        self.assertEqual(len(app.exception),0)
        next(b for b in app.button if b.label=='预约这节课').click();app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(booking.my_lessons(self.student_token)),1)

    def test_32_initial_password_screen_blocks_booking(self):
        from streamlit.testing.v1 import AppTest
        with store.connect() as con:con.execute('UPDATE student_accounts SET must_change=1 WHERE student_id=?',(self.student,))
        app=AppTest.from_file(str(Path(__file__).with_name('student_app.py')))
        app.session_state['student_session']=self.student_token;app.run()
        self.assertEqual(len(app.exception),0)
        self.assertFalse(any(b.label=='预约这节课' for b in app.button))
        self.assertTrue(any(b.label=='修改密码' for b in app.button))


class BookingMigrationTests(unittest.TestCase):
    def setUp(self):credit_tests.CreditTests.setUp(self)
    tearDown=base.OpsTests.tearDown
    buy=credit_tests.CreditTests.buy

    def test_39_rebuild_preserves_ids_rows_foreign_keys_and_auth(self):
        self.buy(3)
        from ops_migrate5 import migrate as migrate5
        migrate5()
        with store.connect() as con:
            packages=[tuple(r) for r in con.execute('SELECT * FROM packages ORDER BY id')]
            ledger=[tuple(r) for r in con.execute('SELECT * FROM credit_ledger ORDER BY id')]
            accounts=[tuple(r) for r in con.execute('SELECT * FROM accounts ORDER BY id')]
        migrate()
        with store.connect() as con:
            self.assertEqual([tuple(r)[:-1] for r in con.execute('SELECT * FROM packages ORDER BY id')],packages)
            self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM credit_ledger ORDER BY id')],ledger)
            self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM accounts ORDER BY id')],accounts)
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(auth.current_user(self.tokens['luo'])['role'],'coach')

    def test_40_failed_migration_rolls_back_table_rebuild(self):
        self.buy(3)
        from ops_migrate5 import migrate as migrate5
        migrate5()
        from test_maintenance import digest
        before=digest(store.DB_PATH)
        original=Path.read_text
        def damaged(path,*args,**kwargs):
            value=original(path,*args,**kwargs)
            return value+'; THIS IS INVALID SQL;' if path.name=='ops_schema6.sql' else value
        with patch.object(Path,'read_text',damaged):
            with self.assertRaises(sqlite3.Error):migrate()
        self.assertEqual(digest(store.DB_PATH),before)
        with store.connect() as con:
            self.assertEqual(con.execute('PRAGMA foreign_key_check').fetchall(),[])
            with self.assertRaises(sqlite3.IntegrityError):con.execute('UPDATE packages SET remaining=-1')
