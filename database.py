import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "tennis.db"


@contextmanager
def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL DEFAULT '',
                pain_points TEXT NOT NULL DEFAULT '',
                goals TEXT NOT NULL DEFAULT '',
                remarks TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS packages (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                kind TEXT NOT NULL
                    CHECK (kind IN ('私教', '陪打')),
                purchased_at TEXT NOT NULL,
                purchased INTEGER NOT NULL
                    CHECK (purchased > 0),
                remaining INTEGER NOT NULL
                    CHECK (remaining >= 0),
                FOREIGN KEY (student_id) REFERENCES students(id)
            );
        """)


def get_students(search=""):
    with connect() as db:
        rows = db.execute("""
            SELECT
                s.*,
                COALESCE((
                    SELECT SUM(p.remaining)
                    FROM packages p
                    WHERE p.student_id = s.id
                    AND p.kind = '私教'
                ), 0) AS private_balance,
                COALESCE((
                    SELECT SUM(p.remaining)
                    FROM packages p
                    WHERE p.student_id = s.id
                    AND p.kind = '陪打'
                ), 0) AS practice_balance
            FROM students s
            ORDER BY s.id DESC
        """).fetchall()

    results = [dict(row) for row in rows]

    keyword = search.strip().casefold()

    if keyword:
        results = [
            student
            for student in results
            if keyword in student["name"].casefold()
            or keyword in f"S{student['id']:03d}".casefold()
        ]

    return results


def get_student(student_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()

    return dict(row) if row else None


def save_student(
    name,
    contact,
    source,
    level,
    pain_points,
    goals,
    remarks,
    student_id=None,
):
    name = name.strip()

    if not name:
        raise ValueError("请填写学员姓名或称呼。")

    values = (
        name,
        contact.strip(),
        source.strip(),
        level.strip(),
        pain_points.strip(),
        goals.strip(),
        remarks.strip(),
    )

    with connect() as db:
        if student_id is None:
            cursor = db.execute("""
                INSERT INTO students (
                    name, contact, source, level,
                    pain_points, goals, remarks
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, values)

            return cursor.lastrowid

        cursor = db.execute("""
            UPDATE students
            SET
                name = ?,
                contact = ?,
                source = ?,
                level = ?,
                pain_points = ?,
                goals = ?,
                remarks = ?
            WHERE id = ?
        """, values + (student_id,))

        if cursor.rowcount != 1:
            raise ValueError("没有找到这位学员，请刷新页面。")

        return student_id


def add_package(student_id, kind, purchased_at, quantity):
    if kind not in ("私教", "陪打"):
        raise ValueError("课程类型不正确。")

    if quantity < 1 or int(quantity) != quantity:
        raise ValueError("购买节数必须是正整数。")

    with connect() as db:
        db.execute("""
            INSERT INTO packages (
                student_id, kind, purchased_at,
                purchased, remaining
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            student_id,
            kind,
            purchased_at,
            int(quantity),
            int(quantity),
        ))


def get_packages(student_id=None):
    sql = """
        SELECT
            p.*,
            s.name AS student_name
        FROM packages p
        JOIN students s ON s.id = p.student_id
    """

    parameters = ()

    if student_id is not None:
        sql += " WHERE p.student_id = ?"
        parameters = (student_id,)

    sql += " ORDER BY p.purchased_at DESC, p.id DESC"

    with connect() as db:
        return [
            dict(row)
            for row in db.execute(sql, parameters).fetchall()
        ]

def delete_package(package_id):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        package = db.execute(
            "SELECT * FROM packages WHERE id = ?",
            (package_id,),
        ).fetchone()

        if package is None:
            raise ValueError("这条课包已经不存在，请刷新页面。")

        if package["remaining"] != package["purchased"]:
            raise ValueError(
                "这条课包已经使用过，暂不支持直接删除。"
            )

        db.execute(
            "DELETE FROM packages WHERE id = ?",
            (package_id,),
        )

def init_lessons():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                kind TEXT NOT NULL
                    CHECK (kind IN ('私教', '陪打')),
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                location TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '待上课',
                reason TEXT NOT NULL DEFAULT '',
                notified_at TEXT,
                absence INTEGER NOT NULL DEFAULT 0,
                charge_due INTEGER NOT NULL DEFAULT 0,
                charged INTEGER NOT NULL DEFAULT 0,
                package_id INTEGER,
                note TEXT NOT NULL DEFAULT '',
                next_goal TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (student_id) REFERENCES students(id),
                FOREIGN KEY (package_id) REFERENCES packages(id)
            )
        """)


def add_lesson(student_id, kind, starts_at, ends_at, location):
    if kind not in ("私教", "陪打"):
        raise ValueError("课程类型不正确。")

    if ends_at <= starts_at:
        raise ValueError("结束时间必须晚于开始时间。")

    if not location.strip():
        raise ValueError("请填写上课地点。")

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        student = db.execute(
            "SELECT name FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()

        if student is None:
            raise ValueError("学员不存在，请刷新页面。")

        balance = db.execute("""
            SELECT
                COUNT(*) AS package_count,
                COALESCE(SUM(remaining), 0) AS remaining
            FROM packages
            WHERE student_id = ?
              AND kind = ?
        """, (student_id, kind)).fetchone()

        if balance["package_count"] == 0:
            raise ValueError(
                f"{student['name']}没有购买{kind}课，"
                "请先到「课包管理」录入购买。"
                "课程未添加。"
            )

        if balance["remaining"] < 1:
            raise ValueError(
                f"{student['name']}的{kind}课已用完，"
                "当前剩余 0 节，请先录入新的购买。"
                "课程未添加。"
            )

        conflict = db.execute("""
            SELECT id
            FROM lessons
            WHERE status != '已取消'
              AND starts_at < ?
              AND ends_at > ?
            LIMIT 1
        """, (ends_at, starts_at)).fetchone()

        if conflict:
            raise ValueError(
                f"与课程 L{conflict['id']:03d} 时间重叠，"
                "请检查时间。"
            )

        db.execute("""
            INSERT INTO lessons (
                student_id, kind, starts_at, ends_at, location
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            student_id,
            kind,
            starts_at,
            ends_at,
            location.strip(),
        ))


def get_lessons(day=None):
    sql = """
        SELECT
            l.*,
            s.name AS student_name,
            COALESCE((
                SELECT SUM(p.remaining)
                FROM packages p
                WHERE p.student_id = l.student_id
                  AND p.kind = l.kind
            ), 0) AS balance
        FROM lessons l
        JOIN students s ON s.id = l.student_id
    """

    parameters = ()

    if day is not None:
        sql += " WHERE substr(l.starts_at, 1, 10) = ?"
        parameters = (day,)

    sql += " ORDER BY l.starts_at DESC, l.id DESC"

    with connect() as db:
        return [
            dict(row)
            for row in db.execute(sql, parameters).fetchall()
        ]


def save_lesson_note(lesson_id, note, next_goal):
    with connect() as db:
        db.execute("""
            UPDATE lessons
            SET note = ?, next_goal = ?
            WHERE id = ?
        """, (note.strip(), next_goal.strip(), lesson_id))


def complete_lesson(lesson_id, note, next_goal):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        lesson = db.execute(
            "SELECT * FROM lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()

        if lesson is None:
            raise ValueError("没有找到这节课。")

        if lesson["status"] != "待上课" or lesson["charged"]:
            raise ValueError("这节课已经处理过，不会重复扣课。")

        package = db.execute("""
            SELECT id
            FROM packages
            WHERE student_id = ?
              AND kind = ?
              AND remaining > 0
            ORDER BY purchased_at ASC, id ASC
            LIMIT 1
        """, (
            lesson["student_id"],
            lesson["kind"],
        )).fetchone()

        if package is None:
            raise ValueError(
                f"{lesson['kind']}余额不足，本次没有扣课，"
                "课程仍为待上课。请先录入购买。"
            )

        db.execute("""
            UPDATE packages
            SET remaining = remaining - 1
            WHERE id = ?
        """, (package["id"],))

        db.execute("""
            UPDATE lessons
            SET
                status = '已完成',
                charge_due = 1,
                charged = 1,
                package_id = ?,
                note = ?,
                next_goal = ?
            WHERE id = ?
        """, (
            package["id"],
            note.strip(),
            next_goal.strip(),
            lesson_id,
        ))
from datetime import datetime


def _deduct_one_package(db, lesson):
    package = db.execute("""
        SELECT id
        FROM packages
        WHERE student_id = ?
          AND kind = ?
          AND remaining > 0
        ORDER BY purchased_at ASC, id ASC
        LIMIT 1
    """, (
        lesson["student_id"],
        lesson["kind"],
    )).fetchone()

    if package is None:
        return False

    db.execute("""
        UPDATE packages
        SET remaining = remaining - 1
        WHERE id = ?
    """, (package["id"],))

    db.execute("""
        UPDATE lessons
        SET charged = 1, package_id = ?
        WHERE id = ?
    """, (package["id"], lesson["id"]))

    return True


def cancel_lesson(
    lesson_id,
    reason,
    notified_at,
    manual_charge=False,
    details="",
):
    notice_time = datetime.fromisoformat(notified_at)

    if notice_time > datetime.now():
        raise ValueError("取消通知时间不能晚于现在。")

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        lesson = db.execute(
            "SELECT * FROM lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()

        if lesson is None:
            raise ValueError("没有找到这节课。")

        if lesson["status"] != "待上课" or lesson["charged"]:
            raise ValueError("这节课已经处理过，请刷新页面。")

        absence = False
        charge_due = False

        if reason == "学员取消":
            start_time = datetime.fromisoformat(
                lesson["starts_at"]
            )

            hours_before = (
                start_time - notice_time
            ).total_seconds() / 3600

            absence = hours_before < 24

            previous_absences = db.execute("""
                SELECT COUNT(*) AS count
                FROM lessons
                WHERE student_id = ?
                  AND absence = 1
            """, (lesson["student_id"],)).fetchone()["count"]

            charge_due = absence and previous_absences >= 1

        elif reason in ("天气", "教练原因", "其他"):
            charge_due = bool(manual_charge)

        else:
            raise ValueError("请选择有效的取消理由。")

        charged = False

        if charge_due:
            charged = _deduct_one_package(db, lesson)

        saved_reason = reason

        if details.strip():
            saved_reason += "：" + details.strip()

        db.execute("""
            UPDATE lessons
            SET
                status = '已取消',
                reason = ?,
                notified_at = ?,
                absence = ?,
                charge_due = ?
            WHERE id = ?
        """, (
            saved_reason,
            notified_at,
            int(absence),
            int(charge_due),
            lesson_id,
        ))

        if charge_due and not charged:
            return "已取消；本次应扣课，但余额不足，已标记未扣课。"

        if charged:
            return "已取消，已扣除 1 节。"

        if absence:
            return "已取消，记为第 1 次缺课，本次不扣课。"

        return "已取消，本次不扣课。"


def settle_unpaid_lesson(lesson_id):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        lesson = db.execute(
            "SELECT * FROM lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()

        if lesson is None:
            raise ValueError("没有找到这节课。")

        if not lesson["charge_due"]:
            raise ValueError("这节课无需扣课。")

        if lesson["charged"]:
            raise ValueError("这节课已经扣过，不会重复扣课。")

        if not _deduct_one_package(db, lesson):
            raise ValueError("余额仍然不足，继续保留未扣课标记。")

        return "已补扣 1 节。"
def delete_lesson(lesson_id):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        lesson = db.execute(
            "SELECT * FROM lessons WHERE id = ?",
            (lesson_id,),
        ).fetchone()

        if lesson is None:
            raise ValueError("这节课已经不存在，请刷新页面。")

        if (
            lesson["status"] != "待上课"
            or lesson["charged"]
            or lesson["charge_due"]
        ):
            raise ValueError(
                "只能删除待上课、尚未扣课的课程。"
            )

        db.execute(
            "DELETE FROM lessons WHERE id = ?",
            (lesson_id,),
        )
