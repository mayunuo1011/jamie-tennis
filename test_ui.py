import shutil
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from test_v1 import db, PROJECT


class PageTests(unittest.TestCase):
    def test_pages_purchase_block_and_history_edit(self):
        original_path = db.DB_PATH
        with tempfile.TemporaryDirectory(prefix='tennis-ui-') as folder:
            project = Path(folder)
            for name in ['app.py', 'database.py', 'dashboard.py', 'student_history.py']:
                shutil.copy2(PROJECT / name, project / name)
            db.DB_PATH = project / 'tennis.db'
            try:
                db.init_db()
                db.init_lessons()
                sid = db.save_student('虚拟学员 Alex', '', '测试介绍', '初学', '正手', '对拉', '')
                db.add_package(sid, '私教', '2026-01-01', 5)
                db.add_lesson(sid, '私教', '2026-01-10T14:00', '2026-01-10T15:00', '测试 Court 2')
                lid = db.get_lessons()[0]['id']
                db.complete_lesson(lid, '正手训练', '击球点前移')
                app = AppTest.from_file(str(project / 'app.py'), default_timeout=30).run()
                self.assertFalse(app.exception, [e.message for e in app.exception])
                for page in ['今日课程', '学员管理', '课包管理', '课程管理']:
                    app.radio[0].set_value(page).run()
                    self.assertFalse(app.exception, [e.message for e in app.exception])
                next(s for s in app.selectbox if s.key == 'new_lesson_kind').set_value('陪打').run()
                self.assertTrue(any('尚未购买' in e.value for e in app.error))
                self.assertTrue(any(b.disabled and '不能添加' in b.label for b in app.button))
                app.radio[0].set_value('学员管理').run()
                next(t for t in app.text_area if t.value == '正手训练').set_value('界面补写测试')
                next(b for b in app.button if b.key == f'FormSubmitter:student_history_note_{sid}_{lid}-保存记录').click().run()
                self.assertFalse(app.exception, [e.message for e in app.exception])
                self.assertEqual(db.get_lessons()[0]['note'], '界面补写测试')
                self.assertEqual(db.get_packages(sid)[0]['remaining'], 4)
            finally:
                db.DB_PATH = original_path
