"""V1 rule checks. Every test uses a disposable database, never tennis.db."""
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(os.environ.get('COACH_PROJECT_DIR', str(Path(__file__).resolve().parent)))
sys.dont_write_bytecode = True
sys.path.insert(0, str(PROJECT))
import database as db


class V1Acceptance(unittest.TestCase):
    def setUp(self):
        self.original_path = db.DB_PATH
        self.temp = tempfile.TemporaryDirectory(prefix='tennis-v1-test-')
        db.DB_PATH = Path(self.temp.name) / 'test.sqlite3'
        db.init_db()
        db.init_lessons()
        self.sid = db.save_student('测试 Alex', '', '测试介绍', '初学', '正手', '对拉', '')

    def tearDown(self):
        db.DB_PATH = self.original_path
        self.temp.cleanup()

    def buy(self, quantity=5, kind='私教', purchased_at='2026-01-01', sid=None):
        db.add_package(sid or self.sid, kind, purchased_at, quantity)
        return max(p['id'] for p in db.get_packages(sid or self.sid))

    def lesson(self, day=10, kind='私教', sid=None):
        db.add_lesson(sid or self.sid, kind, f'2026-01-{day:02d}T14:00', f'2026-01-{day:02d}T15:00', '测试 Court 2')
        return max(l['id'] for l in db.get_lessons())

    def row(self, lid):
        return next(l for l in db.get_lessons() if l['id'] == lid)

    def balance(self, kind='私教', sid=None):
        return sum(p['remaining'] for p in db.get_packages(sid or self.sid) if p['kind'] == kind)

    def late_cancel(self, lid, day):
        return db.cancel_lesson(lid, '学员取消', f'2026-01-{day:02d}T12:00')

    def test_01_same_name_unique_ids(self):
        other = db.save_student('测试 Alex', '', '', '', '', '', '')
        self.assertNotEqual(self.sid, other)
        self.assertEqual(len(db.get_students()), 2)

    def test_02_student_search(self):
        self.assertEqual(db.get_students('alex')[0]['id'], self.sid)
        self.assertEqual(db.get_students(f'S{self.sid:03d}')[0]['id'], self.sid)
        self.assertEqual(db.get_students('不存在的学员'), [])

    def test_03_student_edit_and_persist(self):
        db.save_student('新称呼', '', '朋友', '入门', '发球', '比赛', '备注', student_id=self.sid)
        row = db.get_student(self.sid)
        self.assertEqual((row['name'], row['remarks'], row['goals']), ('新称呼', '备注', '比赛'))

    def test_04_blank_name_rejected(self):
        with self.assertRaises(ValueError):
            db.save_student(' ', '', '', '', '', '', '')

    def test_05_purchase_balances_separate(self):
        self.buy(10)
        self.buy(5, '陪打')
        row = next(s for s in db.get_students() if s['id'] == self.sid)
        self.assertEqual((row['private_balance'], row['practice_balance']), (10, 5))

    def test_06_invalid_purchase_rejected(self):
        for value in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                self.buy(value)
        self.assertEqual(db.get_packages(), [])

    def test_07_no_purchase_cannot_schedule(self):
        with self.assertRaisesRegex(ValueError, '没有购买'):
            self.lesson(kind='陪打')
        self.assertEqual(db.get_lessons(), [])

    def test_08_wrong_type_cannot_schedule(self):
        self.buy(3)
        with self.assertRaisesRegex(ValueError, '没有购买'):
            self.lesson(kind='陪打')

    def test_09_empty_package_cannot_schedule(self):
        self.buy(1)
        db.complete_lesson(self.lesson(), '', '')
        with self.assertRaisesRegex(ValueError, '已用完'):
            self.lesson(11)

    def test_10_schedule_does_not_deduct(self):
        self.buy(5)
        self.lesson()
        self.assertEqual(self.balance(), 5)

    def test_11_conflict_rejected(self):
        self.buy()
        self.lesson()
        with self.assertRaisesRegex(ValueError, '重叠'):
            self.lesson()
        self.assertEqual(len(db.get_lessons()), 1)

    def test_12_adjacent_lessons_allowed(self):
        self.buy()
        self.lesson()
        db.add_lesson(self.sid, '私教', '2026-01-10T15:00', '2026-01-10T16:00', 'Court 2')
        self.assertEqual(len(db.get_lessons()), 2)

    def test_13_invalid_time_and_location(self):
        self.buy()
        for start, end, place in [('2026-01-10T15:00', '2026-01-10T14:00', 'Court 2'), ('2026-01-10T14:00', '2026-01-10T15:00', ' ')]:
            with self.assertRaises(ValueError):
                db.add_lesson(self.sid, '私教', start, end, place)

    def test_14_complete_once(self):
        self.buy(5)
        lid = self.lesson()
        db.complete_lesson(lid, '正手训练', '提前准备')
        with self.assertRaises(ValueError):
            db.complete_lesson(lid, '重试', '')
        self.assertEqual(self.balance(), 4)
        self.assertEqual((self.row(lid)['status'], self.row(lid)['charged']), ('已完成', 1))

    def test_15_note_edit_no_charge(self):
        self.buy()
        lid = self.lesson()
        db.complete_lesson(lid, '', '')
        db.save_lesson_note(lid, '补写记录', '截击')
        self.assertEqual(self.balance(), 4)
        self.assertEqual(self.row(lid)['note'], '补写记录')

    def test_16_fifo(self):
        newer = self.buy(3, purchased_at='2026-01-03')
        older = self.buy(3, purchased_at='2026-01-01')
        lid = self.lesson()
        db.complete_lesson(lid, '', '')
        self.assertEqual(self.row(lid)['package_id'], older)
        self.assertNotEqual(older, newer)

    def test_17_normal_insufficient_balance(self):
        self.buy(1)
        first, second = self.lesson(10), self.lesson(11)
        db.complete_lesson(first, '', '')
        with self.assertRaises(ValueError):
            db.complete_lesson(second, '记录', '')
        self.assertEqual((self.row(second)['status'], self.row(second)['charged']), ('待上课', 0))
        self.assertEqual(self.balance(), 0)

    def test_18_concurrent_completion(self):
        self.buy()
        lid = self.lesson()
        def submit(_):
            try:
                db.complete_lesson(lid, '', '')
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (1, 2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.balance(), 4)

    def test_19_rollback_if_lesson_update_fails(self):
        self.buy()
        lid = self.lesson()
        with db.connect() as connection:
            connection.execute("CREATE TRIGGER fail_complete BEFORE UPDATE OF status ON lessons BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(Exception):
            db.complete_lesson(lid, '', '')
        self.assertEqual(self.balance(), 5)
        self.assertEqual(self.row(lid)['charged'], 0)

    def test_20_exact_24h_no_absence(self):
        self.buy()
        lid = self.lesson()
        db.cancel_lesson(lid, '学员取消', '2026-01-09T14:00')
        self.assertEqual((self.row(lid)['absence'], self.row(lid)['charged']), (0, 0))
        self.assertEqual(self.balance(), 5)

    def test_21_first_second_third_absence(self):
        self.buy()
        lids = [self.lesson(day) for day in (10, 11, 12)]
        for lid, day in zip(lids, (10, 11, 12)):
            self.late_cancel(lid, day)
        self.assertEqual([self.row(lid)['charged'] for lid in lids], [0, 1, 1])
        self.assertEqual(sum(l['absence'] for l in db.get_lessons()), 3)
        self.assertEqual(self.balance(), 3)

    def test_22_unpaid_absence_then_settle_once(self):
        self.buy(1)
        first, second, third = [self.lesson(day) for day in (10, 11, 12)]
        for lid, day in [(first, 10), (second, 11), (third, 12)]:
            self.late_cancel(lid, day)
        row = self.row(third)
        self.assertEqual((row['status'], row['absence'], row['charge_due'], row['charged']), ('已取消', 1, 1, 0))
        with self.assertRaises(ValueError):
            db.settle_unpaid_lesson(third)
        self.buy(2)
        db.settle_unpaid_lesson(third)
        with self.assertRaises(ValueError):
            db.settle_unpaid_lesson(third)
        self.assertEqual(self.balance(), 1)

    def test_23_absence_shared_across_types(self):
        self.buy(2)
        self.buy(2, '陪打')
        self.late_cancel(self.lesson(10), 10)
        self.late_cancel(self.lesson(11, '陪打'), 11)
        self.assertEqual((self.balance(), self.balance('陪打')), (2, 1))

    def test_24_weather_manual_charge_no_absence(self):
        self.buy()
        first, second = self.lesson(10), self.lesson(11)
        db.cancel_lesson(first, '天气', '2026-01-10T12:00', False, '下雨')
        db.cancel_lesson(second, '天气', '2026-01-11T12:00', True)
        self.assertEqual(self.balance(), 4)
        self.assertEqual(sum(l['absence'] for l in db.get_lessons()), 0)
        self.assertIn('下雨', self.row(first)['reason'])

    def test_25_cancelled_cannot_complete_or_cancel_again(self):
        self.buy()
        lid = self.lesson()
        self.late_cancel(lid, 10)
        with self.assertRaises(ValueError):
            db.complete_lesson(lid, '', '')
        with self.assertRaises(ValueError):
            self.late_cancel(lid, 10)
        self.assertEqual(self.balance(), 5)

    def test_26_delete_unused_package(self):
        pid = self.buy()
        db.delete_package(pid)
        self.assertEqual(db.get_packages(), [])

    def test_27_cannot_delete_used_package(self):
        pid = self.buy()
        db.complete_lesson(self.lesson(), '', '')
        with self.assertRaises(ValueError):
            db.delete_package(pid)
        self.assertEqual(self.balance(), 4)

    def test_28_delete_pending_lesson(self):
        self.buy()
        lid = self.lesson()
        db.delete_lesson(lid)
        self.assertEqual(db.get_lessons(), [])
        self.assertEqual(self.balance(), 5)

    def test_29_cannot_delete_completed_or_cancelled(self):
        self.buy()
        first, second = self.lesson(10), self.lesson(11)
        db.complete_lesson(first, '', '')
        self.late_cancel(second, 11)
        for lid in (first, second):
            with self.assertRaises(ValueError):
                db.delete_lesson(lid)
        self.assertEqual(len(db.get_lessons()), 2)

    def test_30_student_isolation_and_date_filter(self):
        other = db.save_student('测试 Sam', '', '', '', '', '', '')
        self.buy()
        self.buy(sid=other)
        first = self.lesson(10)
        self.lesson(11, sid=other)
        db.complete_lesson(first, '', '')
        self.assertEqual((self.balance(), self.balance(sid=other)), (4, 5))
        self.assertEqual(len(db.get_lessons('2026-01-10')), 1)

    def test_31_foreign_key_protection(self):
        with self.assertRaises(Exception):
            self.buy(sid=999999)
        self.assertEqual(db.get_packages(), [])


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    unittest.main(verbosity=2)
