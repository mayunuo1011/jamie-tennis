"""Regression for the actual create path, beyond editing seeded students."""
from pathlib import Path
import unittest
from unittest.mock import patch
import ops_auth as auth
import ops_service as svc
import ops_store as store
import test_calendar as fixture
import test_ops as base


class StudentCreationTests(unittest.TestCase):
    setUp=fixture.CalendarTests.setUp
    tearDown=base.OpsTests.tearDown
    account=fixture.CalendarTests.account

    def test_01_coach_can_create_with_name_only_and_duplicate_names_have_distinct_ids(self):
        a=svc.save_student(self.tokens['luo'],'虚拟新学员')
        b=svc.save_student(self.tokens['luo'],'虚拟新学员')
        self.assertNotEqual(a,b)
        person=svc.student_detail(self.tokens['luo'],a)
        self.assertEqual(person['name'],'虚拟新学员');self.assertEqual(person['contact'],'')

    def test_02_jamie_can_create_public_student_for_luo_without_private_profile(self):
        sid=svc.save_student(self.tokens['jamie'],'虚拟对接学员',coach_id=self.coaches['luo'])
        self.assertEqual(svc.student_detail(self.tokens['luo'],sid)['name'],'虚拟对接学员')
        person=svc.student_detail(self.tokens['jamie'],sid,self.coaches['luo'])
        self.assertNotIn('pain_points',person)

    def test_03_blank_name_rejected(self):
        with self.assertRaises(ValueError):svc.save_student(self.tokens['luo'],'  ')

    def test_04_audit_failure_rolls_back_student_creation(self):
        with store.connect() as con:before=con.execute('SELECT count(*) FROM students').fetchone()[0]
        with patch('ops_auth.audit',side_effect=RuntimeError('simulated')):
            with self.assertRaises(RuntimeError):svc.save_student(self.tokens['luo'],'虚拟回滚学员')
        with store.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM students').fetchone()[0],before)

    def test_05_real_add_student_form(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).with_name('ops_app.py')))
        app.session_state['ops_session']=self.tokens['jamie'];app.run()
        next(r for r in app.radio if r.key=='ops_navigation').set_value('课包管理');app.run()
        next(t for t in app.text_input if t.label=='学员称呼 *').set_value('虚拟表单新增学员')
        next(b for b in app.button if b.label=='添加学员并录入课时').click();app.run()
        self.assertEqual(len(app.exception),0);self.assertEqual(len(app.error),0)
        self.assertEqual(len([s for s in svc.list_students(self.tokens['jamie']) if s['name']=='虚拟表单新增学员']),1)
