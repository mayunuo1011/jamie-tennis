import io
import os
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.dont_write_bytecode = True
PROJECT = Path(os.environ.get('COACH_PROJECT_DIR', str(Path(__file__).resolve().parent)))
sys.path.append(str(PROJECT))
import database as db
from slot_reader import parse_xiaoluo_xlsx
from import_service import hourly_slots, preview_import, import_lessons, preview_sheet_import, import_sheet_lessons


def fixture(names=None, merges=(), header='日期：10.19-10.25', formula=False, colors=None):
    """Minimal in-memory XML fixture, never saved over the source workbook."""
    namespace = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    root = ET.Element('worksheet', xmlns=namespace)
    data = ET.SubElement(root, 'sheetData')
    cells = {'A2': header, 'A25': '小罗课程表'}
    cells.update({f'{c}26': weekday for c, weekday in zip('BCDEFGH', ['周一','周二','周三','周四','周五','周六','周日'])})
    cells.update({f'A{row}': str((row - 19) / 24) for row in range(27, 41)})
    cells.update(names if names is not None else {'F35': 'Alex', 'G34': 'Alex'})
    for address, value in cells.items():
        row = ET.SubElement(data, 'row', r=''.join(c for c in address if c.isdigit()))
        style = {'pink':'1','blue':'2','green':'3'}.get((colors or {}).get(address,'pink'),'0')
        cell = ET.SubElement(row, 'c', r=address, t='inlineStr', s=style)
        if formula and address == 'F35':
            ET.SubElement(cell, 'f').text = '1+1'
        ET.SubElement(ET.SubElement(cell, 'is'), 't').text = str(value)
    if merges:
        items = ET.SubElement(root, 'mergeCells')
        for reference in merges:
            ET.SubElement(items, 'mergeCell', ref=reference)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="'+namespace+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="课程表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', ET.tostring(root))
        archive.writestr('xl/styles.xml', '<styleSheet xmlns="'+namespace+'"><fills><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFC9C7"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF53AEEA"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFC0D86E"/></patternFill></fill></fills><cellXfs><xf fillId="0"/><xf fillId="1"/><xf fillId="2"/><xf fillId="3"/></cellXfs></styleSheet>')
    return output.getvalue()


class ParserTests(unittest.TestCase):
    def test_fixed_layout_dates_and_style_ignored(self):
        result = parse_xiaoluo_xlsx(fixture(), 2026)
        self.assertEqual((result['week_start'], result['week_end']), ('2026-10-19','2026-10-25'))
        self.assertEqual(len(result['records']), 2)

    def test_pink_blue_court_mapping(self):
        rows = hourly_slots(parse_xiaoluo_xlsx(fixture(colors={'F35':'pink','G34':'blue'}),2026))
        self.assertEqual([r['location'] for r in rows], ['Meadows','Grange Club'])

    def test_unknown_fill_not_guessed(self):
        rows = hourly_slots(parse_xiaoluo_xlsx(fixture({'F35':'Alex'},colors={'F35':'green'}),2026))
        self.assertEqual(rows[0]['location'],'')

    def test_merged_hours_keep_each_fill(self):
        rows = hourly_slots(parse_xiaoluo_xlsx(fixture({'H28':'Alex','H29':''}, ['H28:H29'], colors={'H28':'pink','H29':'blue'}),2026))
        self.assertEqual([r['location'] for r in rows],['Meadows','Grange Club'])

    def test_only_xiaoluo_cells(self):
        result = parse_xiaoluo_xlsx(fixture({'F35':'Alex','K35':'other coach','A50':'other block'}), 2026)
        self.assertEqual([r['source_cell'] for r in result['records']], ['F35'])

    def test_adjacent_same_names_are_two_lessons(self):
        result = parse_xiaoluo_xlsx(fixture({'H28':'Moyu','H29':'moyu'}), 2026)
        self.assertEqual(len(hourly_slots(result)), 2)

    def test_merged_individual_is_split_into_hours(self):
        rows = hourly_slots(parse_xiaoluo_xlsx(fixture({'H28':'Alex'}, ['H28:H29']), 2026))
        self.assertEqual(len(rows), 2)
        self.assertEqual([(r['starts_at'][11:16],r['ends_at'][11:16]) for r in rows], [('09:00','10:00'),('10:00','11:00')])

    def test_class_label_excluded(self):
        result = parse_xiaoluo_xlsx(fixture({'G37':'高竞2班'}, ['G37:G38']), 2026)
        self.assertTrue(result['records'][0]['group_label'])
        self.assertEqual(hourly_slots(result), [])

    def test_whitespace_and_empty_cells(self):
        result = parse_xiaoluo_xlsx(fixture({'F35':'\n Alex  ','G34':'\n'}), 2026)
        self.assertEqual([r['name'] for r in result['records']], ['Alex'])

    def test_wrong_or_missing_year(self):
        for year in [2025, None]:
            with self.assertRaises(ValueError):
                parse_xiaoluo_xlsx(fixture(), year)

    def test_cross_year(self):
        result = parse_xiaoluo_xlsx(fixture(header='日期：12.28-1.3'), 2026)
        self.assertEqual(result['week_end'], '2027-01-03')

    def test_invalid_date_range(self):
        with self.assertRaises(ValueError):
            parse_xiaoluo_xlsx(fixture(header='日期：10.19-10.26'), 2026)

    def test_formula_rejected(self):
        with self.assertRaises(ValueError):
            parse_xiaoluo_xlsx(fixture(formula=True), 2026)

    def test_invalid_zip(self):
        with self.assertRaises(ValueError):
            parse_xiaoluo_xlsx(b'invalid file', 2026)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.old_path = db.DB_PATH
        self.temp = tempfile.TemporaryDirectory(prefix='tennis-import-test-')
        db.DB_PATH = Path(self.temp.name) / 'test.db'
        db.init_db()
        db.init_lessons()
        self.sid = db.save_student('Alex','','','','','','')

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def buy(self, kind='私教', qty=5):
        db.add_package(self.sid, kind, '2026-01-01', qty)

    def row(self, day=23, hour=16, sid=None, kind='私教'):
        return dict(student_id=sid or self.sid, kind=kind, starts_at=f'2026-10-{day}T{hour:02}:00', ends_at=f'2026-10-{day}T{hour+1:02}:00', location='Court 2', name='Alex', source_cell='F35')

    def test_preview_does_not_write(self):
        self.buy()
        self.assertEqual(preview_import([self.row()])[0]['result'], '可以导入')
        self.assertEqual(db.get_lessons(), [])

    def test_unpurchased_blocked(self):
        result = import_lessons([self.row()])
        self.assertIn('没有购买', result[0]['result'])
        self.assertEqual(db.get_lessons(), [])

    def test_wrong_type_blocked(self):
        self.buy()
        self.assertIn('没有购买陪打', import_lessons([self.row(kind='陪打')])[0]['result'])

    def test_missing_student_and_location(self):
        self.buy()
        row = self.row()
        row['student_id'] = None
        self.assertIn('未匹配', import_lessons([row])[0]['result'])
        row['student_id'] = self.sid
        row['location'] = ' '
        self.assertIn('地点', import_lessons([row])[0]['result'])

    def test_import_does_not_charge(self):
        self.buy(qty=1)
        results = import_lessons([self.row(), self.row(hour=17)])
        self.assertEqual(sum(r['result']=='已添加' for r in results), 2)
        self.assertEqual(db.get_packages()[0]['remaining'], 1)

    def test_reimport_and_batch_duplicates(self):
        self.buy()
        results = import_lessons([self.row(), self.row()])
        self.assertEqual(sum(r['result']=='已添加' for r in results), 1)
        self.assertIn('重复', import_lessons([self.row()])[0]['result'])
        self.assertEqual(len(db.get_lessons()), 1)

    def test_existing_completed_untouched(self):
        self.buy()
        import_lessons([self.row()])
        lid = db.get_lessons()[0]['id']
        db.complete_lesson(lid, '原笔记', '')
        self.assertIn('重复', import_lessons([self.row()])[0]['result'])
        self.assertEqual(db.get_lessons()[0]['note'], '原笔记')
        self.assertEqual(db.get_packages()[0]['remaining'], 4)

    def test_existing_cancelled_untouched(self):
        self.buy()
        row = self.row(day=10)
        row['starts_at'] = '2026-01-10T16:00'
        row['ends_at'] = '2026-01-10T17:00'
        import_lessons([row])
        db.cancel_lesson(db.get_lessons()[0]['id'], '天气', '2026-01-10T12:00')
        self.assertIn('重复', import_lessons([row])[0]['result'])
        self.assertEqual(db.get_lessons()[0]['status'], '已取消')

    def test_changed_location_requires_manual_handling(self):
        self.buy()
        import_lessons([self.row()])
        row = self.row()
        row['location'] = 'Court 3'
        self.assertIn('地点不同', import_lessons([row])[0]['result'])

    def test_conflict_and_partial_success(self):
        self.buy()
        other = db.save_student('Sam','','','','','','')
        db.add_package(other,'私教','2026-01-01',5)
        results = import_lessons([self.row(), self.row(sid=other), self.row(day=24)])
        self.assertEqual([r['result'] for r in results], ['已添加','与已有课程或本批其他时段重叠','已添加'])

    def test_balance_rechecked_at_submit(self):
        self.buy(qty=1)
        row = self.row()
        self.assertEqual(preview_import([row])[0]['result'], '可以导入')
        db.add_lesson(self.sid,'私教','2026-01-10T14:00','2026-01-10T15:00','Court 2')
        db.complete_lesson(db.get_lessons()[0]['id'],'','')
        self.assertIn('余额不足', import_lessons([row])[0]['result'])
        self.assertEqual(len(db.get_lessons()), 1)

    def test_parallel_import_only_one_row(self):
        self.buy()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: import_lessons([self.row()]), [1,2]))
        self.assertEqual(sum(r[0]['result']=='已添加' for r in results), 1)
        self.assertEqual(len(db.get_lessons()), 1)

    def test_fatal_failure_rolls_back_whole_transaction(self):
        self.buy()
        with db.connect() as connection:
            connection.execute("CREATE TRIGGER fail_import BEFORE INSERT ON lessons WHEN NEW.starts_at='2026-10-23T17:00' BEGIN SELECT RAISE(ABORT, 'test error'); END")
        with self.assertRaises(Exception):
            import_lessons([self.row(), self.row(hour=17)])
        self.assertEqual(db.get_lessons(), [])
        self.assertEqual(db.get_packages()[0]['remaining'], 5)

    def test_ui_preview_and_submit(self):
        from streamlit.testing.v1 import AppTest
        self.buy()
        code = 'from upload_page import render_upload_page\nrender_upload_page()'
        with patch('streamlit.file_uploader', return_value=io.BytesIO(fixture())):
            app = AppTest.from_string(code, default_timeout=30).run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            app.number_input[0].set_value(2026).run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            next(b for b in app.button if b.label=='确认导入 2 节').click().run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            self.assertEqual(len(db.get_lessons()), 2)
            self.assertTrue(next(b for b in app.button if b.label=='确认导入 0 节').disabled)
            self.assertEqual(len(db.get_lessons()), 2)
            self.assertEqual(db.get_packages()[0]['remaining'], 5)

    def test_sheet_new_name_auto_created_without_credits(self):
        row=self.row();row['name']='New Student';row['student_id']=None
        result=import_sheet_lessons([row])
        self.assertTrue(result[0]['new_student'])
        self.assertEqual(len(db.get_students()),2)
        self.assertEqual(db.get_packages(),[])
        self.assertEqual(len(db.get_lessons()),1)
        with self.assertRaises(ValueError):
            db.complete_lesson(db.get_lessons()[0]['id'],'','')

    def test_sheet_preview_does_not_create_student(self):
        row=self.row();row['name']='New Student';row['student_id']=None
        result=preview_sheet_import([row])
        self.assertEqual(result[0]['result'],'可以导入')
        self.assertLess(result[0]['student_id'],0)
        self.assertEqual(len(db.get_students()),1)

    def test_sheet_same_name_casefold_one_profile(self):
        first=self.row();first.update(name='Moyu',student_id=None)
        second=self.row(hour=17);second.update(name='moyu',student_id=None)
        result=import_sheet_lessons([first,second])
        self.assertEqual(sum(r['new_student'] for r in result),1)
        self.assertEqual(len({l['student_id'] for l in db.get_lessons()}),1)

    def test_sheet_existing_student_reused_no_package_required(self):
        row=self.row();row['student_id']=None
        result=import_sheet_lessons([row])
        self.assertFalse(result[0]['new_student'])
        self.assertEqual(result[0]['student_id'],self.sid)
        self.assertEqual(len(db.get_students()),1)
        self.assertEqual(db.get_packages(),[])

    def test_sheet_known_zero_balance_still_schedules(self):
        self.buy(qty=1)
        db.add_lesson(self.sid,'私教','2026-01-10T14:00','2026-01-10T15:00','Court 2')
        db.complete_lesson(db.get_lessons()[0]['id'],'','')
        row=self.row();row['student_id']=None
        self.assertEqual(import_sheet_lessons([row])[0]['result'],'已添加')
        with self.assertRaises(ValueError):
            db.add_lesson(self.sid,'私教','2026-10-24T14:00','2026-10-24T15:00','Court 2')

    def test_sheet_reimport_does_not_create_again(self):
        row=self.row();row.update(name='New Student',student_id=None)
        import_sheet_lessons([row])
        result=import_sheet_lessons([row])
        self.assertIn('重复',result[0]['result'])
        self.assertEqual((len(db.get_students()),len(db.get_lessons())),(2,1))

    def test_sheet_ambiguous_name_requires_choice(self):
        other=db.save_student('Alex','','','','','','')
        row=self.row();row['student_id']=None
        self.assertIn('同名',import_sheet_lessons([row])[0]['result'])
        self.assertEqual(db.get_lessons(),[])
        row['student_id']=other
        self.assertEqual(import_sheet_lessons([row])[0]['result'],'已添加')

    def test_sheet_unknown_location_does_not_create_orphan(self):
        row=self.row();row.update(name='New Student',student_id=None,location='')
        self.assertIn('地点',import_sheet_lessons([row])[0]['result'])
        self.assertEqual(len(db.get_students()),1)
        self.assertEqual(db.get_lessons(),[])

    def test_sheet_creation_rolls_back_on_fatal_error(self):
        with db.connect() as connection:
            connection.execute("CREATE TRIGGER fail_all BEFORE INSERT ON lessons BEGIN SELECT RAISE(ABORT,'test error'); END")
        row=self.row();row.update(name='New Student',student_id=None)
        with self.assertRaises(Exception):
            import_sheet_lessons([row])
        self.assertEqual(len(db.get_students()),1)
        self.assertEqual(db.get_lessons(),[])

    def test_simple_ui_auto_creates_without_mapping_controls(self):
        from streamlit.testing.v1 import AppTest
        content=fixture({'F35':'New Student','G34':'new student'},colors={'F35':'pink','G34':'blue'})
        with patch('streamlit.file_uploader',return_value=io.BytesIO(content)):
            app=AppTest.from_string('from upload_page import render_upload_page\nrender_upload_page()',default_timeout=30).run()
            app.number_input[0].set_value(2026).run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertEqual(len(app.selectbox),1)
            self.assertEqual(len(app.text_input),0)
            next(b for b in app.button if b.label=='确认导入 2 节').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertEqual((len(db.get_students()),len(db.get_lessons())),(2,2))
            self.assertEqual({l['location'] for l in db.get_lessons()},{'Meadows','Grange Club'})
            self.assertEqual(db.get_packages(),[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
