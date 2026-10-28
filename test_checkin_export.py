"""Regression checks use temporary databases only."""
from datetime import date, datetime, timedelta
from io import BytesIO
from unittest.mock import patch
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import lesson_export as exports
import user_service as svc
import user_store as store
import test_user_workspace as base

NOTE,GOAL=base.NOTE,base.GOAL


class CheckinExportTests(unittest.TestCase):
    setUp=base.WorkspaceTests.setUp
    tearDown=base.WorkspaceTests.tearDown
    lesson=base.WorkspaceTests.lesson

    def completed(self, day=None, hour=14, sid=None):
        lid=self.lesson(day or date(2026,1,12),hour,sid)
        svc.check_in(lid,NOTE,GOAL)
        return lid

    def test_exactly_ten_rejected_eleven_accepted(self):
        lid=self.lesson()
        with self.assertRaisesRegex(ValueError,'目前 10 个'):
            svc.check_in(lid,'中'*10,'中'*11)
        self.assertEqual(svc.lesson(lid)['checkin_done'],0)
        svc.check_in(lid,'中'*11,'文'*11)
        self.assertEqual(svc.lesson(lid)['checkin_done'],1)

    def test_each_field_required_independently(self):
        lid=self.lesson()
        for note,goal in [('',GOAL),(NOTE,''),('中'*10,GOAL),(NOTE,'文'*10),('   ','\n')]:
            with self.subTest(note=note,goal=goal):
                with self.assertRaises(ValueError):svc.check_in(lid,note,goal)
                self.assertEqual(svc.lesson(lid)['checkin_done'],0)
                self.assertIsNone(svc.lesson(lid)['checked_in_at'])

    def test_only_chinese_characters_count(self):
        self.assertEqual(svc.chinese_count('中文 Tennis 123 ，。!? \n🎾'),2)
        self.assertEqual(svc.chinese_count('正手訓練𠮷㐀'),6)
        lid=self.lesson()
        with self.assertRaises(ValueError):svc.check_in(lid,'中'*10+'abc 123 ！！',GOAL)
        svc.check_in(lid,'中'*11+'ABC',GOAL)

    def test_invalid_edit_keeps_entire_previous_row(self):
        lid=self.completed()
        before=svc.lesson(lid)
        with self.assertRaises(ValueError):svc.check_in(lid,NOTE,'短')
        self.assertEqual(svc.lesson(lid),before)

    def test_existing_invalid_record_not_rewritten(self):
        lid=self.lesson()
        with store.connect() as connection:
            connection.execute('UPDATE lessons SET checkin_done=1,note=?,next_goal=? WHERE id=?',('', '',lid))
        before=svc.lesson(lid)
        store.initialize()
        self.assertEqual(svc.lesson(lid),before)
        with self.assertRaises(ValueError):svc.check_in(lid,'','')

    def test_export_selection_omits_future_canceled_moved_unchecked_incomplete_other_week(self):
        monday=date(2026,1,12)
        good=self.completed(monday,9)
        canceled=self.completed(monday,10);svc.cancel(canceled,'天气',True)
        moved=self.completed(monday,11)
        svc.reschedule(moved,None,datetime.min.time(),datetime.min.replace(hour=1).time(),'Meadows')
        self.lesson(monday,12)
        incomplete=self.lesson(monday,13)
        with store.connect() as connection:
            connection.execute('UPDATE lessons SET checkin_done=1,note=? WHERE id=?',('旧短记录',incomplete))
        self.completed(monday,16)
        self.completed(monday+timedelta(days=7),9)
        eligible=exports.eligible_lessons(svc.lessons(),monday,datetime(2026,1,12,15))
        self.assertEqual([l['id'] for l in eligible],[good])

    def test_selection_deduplicates_and_ignores_unknown_ids(self):
        lid=self.completed()
        selected=exports.select_records(svc.lessons(),[lid,lid,999],date(2026,1,12))
        self.assertEqual([r['id'] for r in selected],[lid])
        self.assertEqual(exports.select_records(svc.lessons(),[],date(2026,1,12)),[])

    def test_export_groups_by_id_not_duplicate_name_and_sorts_lessons(self):
        other=svc.save_student('Alex')
        first=self.completed(hour=9)
        second=self.completed(hour=10,sid=other)
        third=self.completed(hour=11)
        groups=exports.grouped_records([svc.lesson(third),svc.lesson(second),svc.lesson(first)])
        self.assertEqual([[l['id'] for l in g] for g in groups],[[first,third],[second]])

    def test_text_contains_training_but_no_private_or_financial_fields(self):
        svc.save_student('Alex',contact='PRIVATE_PHONE',remarks='PRIVATE_REMARK',sid=self.sid)
        lid=self.completed()
        row=svc.lesson(lid);row['balance']='PRIVATE_BALANCE'
        text=exports.export_text([row])
        for value in ['Alex','2026-01-12','14:00','15:00','Meadows',NOTE,GOAL]:
            self.assertIn(value,text)
        for value in ['PRIVATE_PHONE','PRIVATE_REMARK','PRIVATE_BALANCE','扣课','剩余','S001','L001']:
            self.assertNotIn(value,text)

    def test_txt_utf8_bom_and_newlines(self):
        rows=[svc.lesson(self.completed())]
        data=exports.export_txt(rows)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(data.decode('utf-8-sig').replace('\r\n','\n'),exports.export_text(rows))

    def test_word_editable_xml_and_special_characters(self):
        lid=self.completed()
        svc.check_in(lid,NOTE+' <测试>&\n第二行训练记录',GOAL)
        with ZipFile(BytesIO(exports.export_docx([svc.lesson(lid)]))) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():ET.fromstring(archive.read(name))
            doc=ET.fromstring(archive.read('word/document.xml'))
            text=''.join(doc.itertext())
            for value in ['Alex',NOTE,'<测试>&','第二行训练记录',GOAL]:self.assertIn(value,text)
            self.assertTrue(doc.findall('.//{'+exports.W+'}br'))
            self.assertNotIn('PRIVATE_',text)

    def test_export_does_not_modify_database(self):
        self.completed()
        with store.connect() as connection:
            before=list(connection.iterdump())
        rows=exports.select_records(svc.lessons(),[svc.lessons()[0]['id']],date(2026,1,12))
        exports.export_txt(rows);exports.export_docx(rows)
        with store.connect() as connection:self.assertEqual(list(connection.iterdump()),before)


class ExportPageTests(unittest.TestCase):
    setUp=base.WorkspaceTests.setUp
    tearDown=base.WorkspaceTests.tearDown
    lesson=base.WorkspaceTests.lesson
    app=base.PageTests.app

    def test_empty_submit_is_not_checkin_and_reports_both_fields(self):
        from streamlit.testing.v1 import AppTest
        lid=self.lesson()
        app=AppTest.from_string(f'from user_views import lesson_dialog\nlesson_dialog({lid})').run()
        next(b for b in app.button if b.label=='保存并打卡').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(svc.lesson(lid)['checkin_done'],0)
        self.assertIn('下次目标',app.error[0].value)
        self.assertIn('课后总结',app.error[0].value)

    def test_profile_invalid_edit_preserves_record(self):
        from streamlit.testing.v1 import AppTest
        lid=self.lesson();svc.check_in(lid,NOTE,GOAL)
        before=svc.lesson(lid)
        app=AppTest.from_string(f'from user_views import profile_dialog\nprofile_dialog({self.sid})').run()
        next(t for t in app.text_area if t.label=='下次目标').set_value('')
        next(b for b in app.button if b.label=='保存修改').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertEqual(svc.lesson(lid),before)

    def test_upcoming_cards_have_no_view_button(self):
        self.lesson(svc.today_london()+timedelta(days=1))
        app=self.app()
        self.assertTrue(any('Alex' in m.value for m in app.markdown))
        self.assertFalse(any(b.label=='查看课程' or (b.key or '').startswith('upcoming_') for b in app.button))

    def test_export_ui_preview_and_two_downloads(self):
        from streamlit.testing.v1 import AppTest
        lid=self.lesson();svc.check_in(lid,NOTE,GOAL)
        with patch('user_service.now_london',return_value=datetime.combine(self.monday+timedelta(days=6),datetime.max.time()).replace(tzinfo=svc.LONDON)):
            app=self.app()
            self.assertEqual(len(app.multiselect),1)
            app.multiselect[0].select(lid).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertTrue(any(NOTE in c.value and GOAL in c.value for c in app.code))
            self.assertEqual(len(app.get('download_button')),2)
            app.button(key='next_week').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.code),0)


if __name__=='__main__':
    unittest.main(verbosity=2)
