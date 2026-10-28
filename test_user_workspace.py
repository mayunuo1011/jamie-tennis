import io
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.dont_write_bytecode=True
PROJECT=Path(os.environ.get('COACH_PROJECT_DIR',str(Path(__file__).resolve().parent)))
sys.path.append(str(PROJECT))
import database as legacy
import user_store as store
import user_service as svc
from user_import_service import preview_sheet_import

NOTE='今天重点练习正手击球与身体重心转移'
GOAL='下次继续练习发球动作并提高落点稳定性'
EDITED='修改后的训练记录补充脚步移动与击球细节'


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.old_path=store.DB_PATH
        self.temp=tempfile.TemporaryDirectory(prefix='tennis-user-test-')
        store.configure(Path(self.temp.name)/'tennis.db')
        store.initialize()
        self.sid=svc.save_student('Alex')
        self.monday=svc.week_start()

    def tearDown(self):
        store.configure(self.old_path)
        self.temp.cleanup()

    def lesson(self,day=None,hour=14,sid=None,place='Meadows'):
        day=day or self.monday
        return svc.create_lesson(sid or self.sid,'私教',f'{day}T{hour:02}:00',f'{day}T{hour+1:02}:00',place)

    def financial_snapshot(self):
        return store.rows('SELECT * FROM packages ORDER BY id'),store.rows('SELECT id,charged,charge_due,package_id FROM lessons ORDER BY id')

    def test_01_schema_idempotent(self):
        self.assertIsNone(store.initialize())
        columns={r['name'] for r in store.rows('PRAGMA table_info(lessons)')}
        self.assertIn('checkin_done',columns)
        self.assertIn('original_lesson_id',columns)

    def test_02_unique_name_edit_optional_fields(self):
        second=svc.save_student('Alex')
        self.assertNotEqual(second,self.sid)
        svc.save_student('Alex Updated',remarks='备注',sid=self.sid)
        self.assertEqual(svc.student(self.sid)['remarks'],'备注')
        self.assertEqual(svc.student(self.sid)['contact'],'')
        with self.assertRaises(ValueError):svc.save_student(' ')

    def test_03_join_date_new_known_old_nullable(self):
        self.assertEqual(svc.student(self.sid)['joined_at'],svc.today_london().isoformat())
        svc.save_student('Alex',joined_at='2025-01-01',sid=self.sid)
        self.assertEqual(svc.student(self.sid)['joined_at'],'2025-01-01')

    def test_04_star_and_alphabetical_sort(self):
        z=svc.save_student('Zoe');l=svc.save_student('李同学');w=svc.save_student('王同学');zh=svc.save_student('张同学')
        self.assertEqual([p['id'] for p in svc.students()],[self.sid,l,w,z,zh])
        svc.set_star(z,True)
        self.assertEqual(svc.students()[0]['id'],z)
        self.assertEqual(svc.students('zoe')[0]['id'],z)

    def test_05_time_based_attendance_and_manual_override(self):
        lid=self.lesson(date(2026,1,12))
        row=svc.lesson(lid)
        self.assertEqual(svc.attendance(row,datetime(2026,1,12,14,30)),'没上')
        self.assertEqual(svc.attendance(row,datetime(2026,1,12,15,0)),'上了')
        svc.cancel(lid,'天气',True)
        self.assertEqual(svc.attendance(svc.lesson(lid),datetime(2030,1,1)),'已取消')

    def test_06_timezone_attendance_summer_offset(self):
        lid=self.lesson(date(2026,7,6))
        self.assertEqual(svc.attendance(svc.lesson(lid),datetime(2026,7,6,14,0,tzinfo=timezone.utc)),'上了')
        self.assertEqual(svc.LONDON.key,'Europe/London')

    def test_07_checkin_and_edit_never_touch_credit(self):
        legacy.add_package(self.sid,'私教','2026-01-01',5)
        lid=self.lesson()
        before=self.financial_snapshot()
        svc.check_in(lid,NOTE,GOAL)
        stamp=svc.lesson(lid)['checked_in_at']
        svc.check_in(lid,EDITED,GOAL)
        row=svc.lesson(lid)
        self.assertEqual(row['checkin_done'],1)
        self.assertEqual(row['checked_in_at'],stamp)
        self.assertEqual(row['note'],EDITED)
        self.assertEqual(self.financial_snapshot(),before)

    def test_08_empty_checkin_rejected_without_package(self):
        lid=self.lesson()
        with self.assertRaises(ValueError):svc.check_in(lid,'','')
        self.assertEqual(svc.lesson(lid)['checkin_done'],0)
        self.assertEqual(store.rows('SELECT * FROM packages'),[])

    def test_09_second_absence_highlight_count(self):
        a,b=self.lesson(hour=14),self.lesson(hour=15)
        before=self.financial_snapshot()
        svc.cancel(a,'学员取消',False)
        self.assertEqual(svc.student(self.sid)['absence_count'],1)
        svc.cancel(b,'学员取消',False)
        self.assertEqual(svc.student(self.sid)['absence_count'],2)
        self.assertEqual(svc.lesson(a)['absence_count'],2)
        self.assertEqual(self.financial_snapshot(),before)

    def test_10_advance_weather_not_absence(self):
        a,b=self.lesson(hour=14),self.lesson(hour=15)
        svc.cancel(a,'学员取消',True)
        svc.cancel(b,'天气',False)
        self.assertEqual(svc.student(self.sid)['absence_count'],0)
        self.assertEqual(svc.lesson(a)['cancellation_24h'],1)

    def test_11_repeat_cancel_does_not_count_twice(self):
        lid=self.lesson()
        svc.cancel(lid,'学员取消',False)
        with self.assertRaises(ValueError):svc.cancel(lid,'学员取消',False)
        self.assertEqual(svc.student(self.sid)['absence_count'],1)

    def test_12_historical_fix_recomputes_count(self):
        lids=[self.lesson(hour=h) for h in (14,15)]
        for lid in lids:svc.cancel(lid,'学员取消',False)
        with store.connect() as connection:
            connection.execute('UPDATE lessons SET absence=0,cancellation_24h=1 WHERE id=?',(lids[0],))
        self.assertEqual(svc.student(self.sid)['absence_count'],1)
        self.assertEqual(svc.lesson(lids[1])['absence_count'],1)

    def test_13_reschedule_next_week_preserves_original(self):
        lid=self.lesson()
        next_week=self.monday+timedelta(days=7)
        new=svc.reschedule(lid,next_week,time(16),time(17),'Grange Club','调整时间')
        original,target=svc.lesson(lid),svc.lesson(new)
        self.assertEqual(original['status'],'已改期')
        self.assertEqual(target['original_lesson_id'],lid)
        self.assertEqual(target['location'],'Grange Club')
        self.assertEqual([l['id'] for l in svc.lessons(next_week)],[new])
        self.assertEqual([l['id'] for l in svc.lessons(self.monday)],[lid])
        self.assertEqual(target['charged'],0)

    def test_14_pending_reschedule_then_assign(self):
        lid=self.lesson()
        self.assertIsNone(svc.reschedule(lid,None,time(10),time(11),'Grange Club'))
        row=svc.lesson(lid)
        self.assertEqual(row['reschedule_pending'],1)
        self.assertEqual(row['proposed_start'],'10:00')
        new=svc.reschedule(lid,self.monday+timedelta(days=7),time(10),time(11),'Grange Club')
        self.assertEqual(svc.lesson(lid)['reschedule_pending'],0)
        self.assertEqual(svc.lesson(new)['original_lesson_id'],lid)

    def test_15_reschedule_conflict_rolls_back(self):
        lid=self.lesson(hour=14)
        self.lesson(hour=16)
        with self.assertRaises(ValueError):svc.reschedule(lid,self.monday,time(16),time(17),'Meadows')
        self.assertEqual(svc.lesson(lid)['status'],'待上课')
        self.assertEqual(len(svc.lessons()),2)

    def test_16_same_schedule_no_change_rejected(self):
        lid=self.lesson()
        with self.assertRaises(ValueError):svc.reschedule(lid,self.monday,time(14),time(15),'Meadows')

    def test_17_repeated_and_parallel_reschedule(self):
        lid=self.lesson()
        def submit(_):
            try:
                return svc.reschedule(lid,self.monday+timedelta(days=7),time(14),time(15),'Meadows') is not None
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(submit,[1,2])),1)
        self.assertEqual(len(svc.lessons()),2)

    def test_18_cannot_reschedule_cancelled(self):
        lid=self.lesson()
        svc.cancel(lid,'天气',True)
        with self.assertRaises(ValueError):svc.reschedule(lid,None,time(14),time(15),'Meadows')

    def test_19_reschedule_money_preserved(self):
        legacy.add_package(self.sid,'私教','2026-01-01',5)
        lid=self.lesson()
        legacy.complete_lesson(lid,'旧记录','旧目标')
        before=store.rows('SELECT * FROM packages')
        old_flags=tuple(svc.lesson(lid)[k] for k in ('charged','charge_due','package_id'))
        new=svc.reschedule(lid,self.monday+timedelta(days=7),time(14),time(15),'Grange Club')
        self.assertEqual(store.rows('SELECT * FROM packages'),before)
        self.assertEqual(tuple(svc.lesson(lid)[k] for k in ('charged','charge_due','package_id')),old_flags)
        self.assertEqual(svc.lesson(new)['charged'],0)

    def test_20_daily_notes_rollover_preserves_history(self):
        svc.save_daily_note('2026-10-20','当天事项')
        self.assertEqual(svc.daily_note('2026-10-21')['body'],'')
        self.assertEqual(svc.daily_note('2026-10-20')['body'],'当天事项')
        svc.save_daily_note('2026-10-20','历史修正')
        self.assertEqual(svc.daily_note('2026-10-20')['body'],'历史修正')

    def test_21_import_new_student_and_join_date_no_credit(self):
        day=self.monday.isoformat()
        row={'name':'Moyu','student_id':None,'kind':'私教','starts_at':day+'T09:00','ends_at':day+'T10:00','location':'Meadows'}
        self.assertEqual(preview_sheet_import([row])[0]['result'],'可以导入')
        self.assertEqual(len(svc.students()),1)
        result=svc.import_bookings([row])
        self.assertTrue(result[0]['new_student'])
        p=svc.student(result[0]['student_id'])
        self.assertEqual(p['joined_at'],svc.today_london().isoformat())
        self.assertEqual(store.rows('SELECT * FROM packages'),[])
        svc.import_bookings([row])
        self.assertEqual(len(svc.lessons()),1)

    def test_22_import_can_reuse_freed_rescheduled_slot(self):
        lid=self.lesson()
        svc.reschedule(lid,self.monday+timedelta(days=7),time(14),time(15),'Meadows')
        row={'name':'Sam','student_id':None,'kind':'私教','starts_at':f'{self.monday}T14:00','ends_at':f'{self.monday}T15:00','location':'Meadows'}
        self.assertEqual(svc.import_bookings([row])[0]['result'],'已添加')


class MigrationTests(unittest.TestCase):
    def test_existing_data_backup_and_credit_unchanged(self):
        old_path=store.DB_PATH
        with tempfile.TemporaryDirectory() as folder:
            try:
                store.configure(Path(folder)/'tennis.db')
                legacy.init_db();legacy.init_lessons()
                sid=legacy.save_student('旧学员','','','','','','')
                legacy.add_package(sid,'私教','2026-01-01',5)
                legacy.add_lesson(sid,'私教','2026-01-12T14:00','2026-01-12T15:00','Meadows')
                legacy.complete_lesson(legacy.get_lessons()[0]['id'],'旧笔记','旧目标')
                before=store.rows('SELECT * FROM packages')
                backup=store.initialize()
                self.assertTrue(Path(backup).is_file())
                self.assertEqual(store.rows('SELECT * FROM packages'),before)
                self.assertEqual(svc.lessons()[0]['checkin_done'],1)
                self.assertIsNone(svc.student(sid)['joined_at'])
                with closing(sqlite3.connect(backup)) as connection:
                    self.assertNotIn('checkin_done',{r[1] for r in connection.execute('PRAGMA table_info(lessons)')})
                self.assertIsNone(store.initialize())
            finally:
                store.configure(old_path)


class PageTests(unittest.TestCase):
    setUp=WorkspaceTests.setUp
    tearDown=WorkspaceTests.tearDown
    lesson=WorkspaceTests.lesson
    def app(self):
        from streamlit.testing.v1 import AppTest
        path=Path(__file__).resolve().parent/'user_app.py'
        app=AppTest.from_file(str(path),default_timeout=30).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        return app

    def test_ui_four_pages_no_financial_controls(self):
        self.lesson()
        app=self.app()
        self.assertEqual(app.sidebar.radio[0].value,'本周课程')
        for page in ['本周课程','学员档案','课程管理','课表导入']:
            app.sidebar.radio[0].set_value(page).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            labels=' '.join(b.label for b in app.button)
            for forbidden in ['扣课','补扣','课包','购买','退款']:
                self.assertNotIn(forbidden,labels)

    def test_ui_student_create_and_search(self):
        app=self.app()
        app.sidebar.radio[0].set_value('学员档案').run()
        next(t for t in app.text_input if t.label=='姓名 / 称呼 *').set_value('新学员')
        next(b for b in app.button if b.label=='新增学员').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(len(svc.students()),2)
        next(t for t in app.text_input if t.key=='user_student_search').set_value('新学员').run()
        self.assertEqual(app.dataframe[0].value.iloc[0]['称呼'],'新学员')

    def test_ui_week_navigation(self):
        self.lesson()
        self.lesson(self.monday+timedelta(days=7))
        app=self.app()
        next(b for b in app.button if b.key=='next_week').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(app.dataframe[0].value.iloc[0]['日期'],(self.monday+timedelta(days=7)).isoformat())
        next(b for b in app.button if b.key=='current_week').click().run()
        self.assertEqual(app.dataframe[0].value.iloc[0]['日期'],self.monday.isoformat())

    def test_ui_sticky_note_save_across_pages(self):
        app=self.app()
        body=next(t for t in app.text_area if t.key and t.key.startswith('daily_note_body_'))
        body.set_value('带练习球').run()
        self.assertEqual(svc.daily_note(svc.today_london().isoformat())['body'],'带练习球')
        app.sidebar.radio[0].set_value('学员档案').run()
        self.assertEqual(next(t for t in app.text_area if t.key and t.key.startswith('daily_note_body_')).value,'带练习球')
        next(b for b in app.button if b.key=='toggle_daily_notes').click().run()
        self.assertTrue(app.session_state['notes_minimized'])

    def test_ui_cancel_has_no_notification_time_or_deduction(self):
        lid=self.lesson()
        app=self.app();app.sidebar.radio[0].set_value('课程管理').run()
        self.assertFalse(any(t.form_id==f'user_cancel_{lid}' for t in app.time_input))
        self.assertFalse(any(t.form_id==f'user_cancel_{lid}' for t in app.date_input))
        next(b for b in app.button if b.key==f'FormSubmitter:user_cancel_{lid}-确认取消').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.lesson(lid)['absence'],1)
        self.assertEqual(svc.lesson(lid)['charged'],0)

    def test_ui_reschedule_next_week(self):
        lid=self.lesson()
        app=self.app();app.sidebar.radio[0].set_value('课程管理').run()
        next(d for d in app.date_input if d.label=='改到哪一天（未确定可留空）').set_value(self.monday+timedelta(days=7))
        next(t for t in app.text_input if t.label=='新地点').set_value('Grange Club')
        next(b for b in app.button if b.key==f'FormSubmitter:user_move_form_week_{lid}-保存改期').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(len(svc.lessons(self.monday+timedelta(days=7))),1)

    def test_ui_lesson_dialog_checkin_without_payment(self):
        from streamlit.testing.v1 import AppTest
        lid=self.lesson()
        app=AppTest.from_string(f'from user_views import lesson_dialog\nlesson_dialog({lid})',default_timeout=30).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        next(t for t in app.text_area if t.label=='课后总结 / 训练记录').set_value(NOTE)
        next(t for t in app.text_area if t.label=='下次目标').set_value(GOAL)
        next(b for b in app.button if b.label=='保存并打卡').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.lesson(lid)['checkin_done'],1)
        self.assertEqual(svc.lesson(lid)['charged'],0)

    def test_ui_profile_modal_star_and_history(self):
        from streamlit.testing.v1 import AppTest
        lid=self.lesson();svc.check_in(lid,NOTE,GOAL)
        app=AppTest.from_string(f'from user_views import profile_dialog\nprofile_dialog({self.sid})',default_timeout=30).run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any(NOTE==m.value for m in app.markdown))
        next(b for b in app.button if b.key==f'profile_star_{self.sid}').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertEqual(svc.student(self.sid)['starred'],1)

    def test_ui_row_click_and_save_keeps_correct_lesson(self):
        lid=self.lesson()
        event=SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        with patch('streamlit.dataframe',return_value=event):
            app=self.app()
            self.assertTrue(any(t.label=='课后总结 / 训练记录' for t in app.text_area))
            next(t for t in app.text_area if t.label=='课后总结 / 训练记录').set_value(NOTE)
            next(t for t in app.text_area if t.label=='下次目标').set_value(GOAL)
            next(b for b in app.button if b.label=='保存并打卡').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertEqual(svc.lesson(lid)['note'],NOTE)

    def test_ui_star_resort_keeps_same_profile(self):
        other=svc.save_student('Zoe')
        event=SimpleNamespace(selection=SimpleNamespace(rows=[1]))
        with patch('streamlit.dataframe',return_value=event):
            app=self.app();app.sidebar.radio[0].set_value('学员档案').run()
            self.assertTrue(any("Zoe's Profile" in h.value for h in app.subheader))
            next(b for b in app.button if b.key==f'profile_star_{other}').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertTrue(any("Zoe's Profile" in h.value for h in app.subheader))
            self.assertEqual(svc.students()[0]['id'],other)

    def test_ui_note_date_rollover_history_retained(self):
        from streamlit.testing.v1 import AppTest
        with patch('user_service.today_london',return_value=date(2026,10,20)):
            app=AppTest.from_string('from user_views import sticky_notes\nsticky_notes()',default_timeout=30).run()
            app.text_area[0].set_value('昨天的事项').run()
        with patch('user_service.today_london',return_value=date(2026,10,21)):
            app.run()
            self.assertEqual(app.text_area[0].value,'')
            app.date_input[0].set_value(date(2026,10,20)).run()
            self.assertEqual(app.text_area[0].value,'昨天的事项')
        self.assertEqual(svc.daily_note('2026-10-20')['body'],'昨天的事项')


if __name__=='__main__':
    unittest.main(verbosity=2)
