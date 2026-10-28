"""Coach actions: records, cancellations and rescheduling. No credit mutations."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import user_store as store

LONDON = ZoneInfo('Europe/London')


def now_london():
    return datetime.now(LONDON)


def today_london():
    return now_london().date()


def week_start(day=None):
    day = day or today_london()
    return day - timedelta(days=day.weekday())


def attendance(lesson, now=None):
    if lesson['status'] == '已取消':
        return '已取消'
    if lesson['status'] == '已改期':
        return '已改期 · 日期待定' if lesson.get('reschedule_pending') else '已改期'
    local_now = now or now_london()
    if local_now.tzinfo is not None:
        local_now = local_now.astimezone(LONDON).replace(tzinfo=None)
    if lesson.get('manual_attendance'):
        if lesson.get('attendance_confirmed') or lesson['status']=='已完成':return '上了'
        return '待确认上课' if datetime.fromisoformat(lesson['ends_at'])<=local_now else '没上'
    return '上了' if datetime.fromisoformat(lesson['ends_at']) <= local_now else '没上'


def _initials(name):
    bands = [(-20319,'a'),(-20284,'b'),(-19776,'c'),(-19219,'d'),(-18711,'e'),(-18527,'f'),(-18240,'g'),(-17923,'h'),(-17418,'j'),(-16475,'k'),(-16213,'l'),(-15641,'m'),(-15166,'n'),(-14923,'o'),(-14915,'p'),(-14631,'q'),(-14150,'r'),(-14091,'s'),(-13319,'t'),(-12839,'w'),(-12557,'x'),(-11848,'y'),(-11056,'z')]
    parts = []
    for letter in name.casefold():
        try:
            encoded = letter.encode('gb2312')
        except UnicodeEncodeError:
            parts.append(letter)
            continue
        if len(encoded) == 2:
            value = encoded[0] * 256 + encoded[1] - 65536
            if -20319 <= value <= -10247:
                letter = next(initial for boundary, initial in reversed(bands) if value >= boundary)
        parts.append(letter)
    return ''.join(parts)


def students(search=''):
    people = store.rows('SELECT s.*,COALESCE((SELECT SUM(l.absence) FROM lessons l WHERE l.student_id=s.id),0) absence_count FROM students s')
    keyword = search.strip().casefold()
    people = [p for p in people if not keyword or keyword in p['name'].casefold() or keyword in f"S{p['id']:03d}".casefold()]
    return sorted(people, key=lambda p: (not p['starred'], _initials(p['name']), p['name'].casefold(), p['id']))


def student(sid):
    return next((p for p in students() if p['id'] == sid), None)


def save_student(name, contact='', source='', level='', pain_points='', goals='', remarks='', joined_at=None, sid=None):
    name = name.strip()
    if not name:
        raise ValueError('请填写学员称呼。')
    if joined_at:
        date.fromisoformat(joined_at)
    with store.connect() as connection:
        values = (name,contact.strip(),source.strip(),level.strip(),pain_points.strip(),goals.strip(),remarks.strip(),joined_at)
        if sid is None:
            return connection.execute('INSERT INTO students(name,contact,source,level,pain_points,goals,remarks,joined_at) VALUES(?,?,?,?,?,?,?,?)', values[:-1] + (joined_at or today_london().isoformat(),)).lastrowid
        if connection.execute('UPDATE students SET name=?,contact=?,source=?,level=?,pain_points=?,goals=?,remarks=?,joined_at=? WHERE id=?', values+(sid,)).rowcount != 1:
            raise ValueError('学员不存在。')
        return sid


def set_star(sid, starred):
    with store.connect() as connection:
        connection.execute('UPDATE students SET starred=? WHERE id=?',(int(starred),sid))


LESSON_QUERY = '''SELECT l.*,s.name student_name,
 COALESCE((SELECT SUM(a.absence) FROM lessons a WHERE a.student_id=l.student_id),0) absence_count,
 (SELECT c.starts_at FROM lessons c WHERE c.original_lesson_id=l.id LIMIT 1) moved_to
 FROM lessons l JOIN students s ON s.id=l.student_id'''


def lessons(start=None, sid=None):
    clauses, values = [], []
    if start:
        if isinstance(start, str):
            start = date.fromisoformat(start)
        clauses.append('l.starts_at>=? AND l.starts_at<?')
        values.extend([start.isoformat(),(start+timedelta(days=7)).isoformat()])
    if sid:
        clauses.append('l.student_id=?')
        values.append(sid)
    return store.rows(LESSON_QUERY + (' WHERE '+' AND '.join(clauses) if clauses else '') + ' ORDER BY l.starts_at,l.id', values)


def lesson(lid):
    found = store.rows(LESSON_QUERY+' WHERE l.id=?',(lid,))
    if not found:
        raise ValueError('课程不存在。')
    return found[0]


def _conflict(connection, start, end, excluded=None):
    row = connection.execute("SELECT id FROM lessons WHERE status NOT IN ('已取消','已改期') AND starts_at<? AND ends_at>? AND id!=? LIMIT 1",(end,start,excluded or -1)).fetchone()
    if row:
        raise ValueError(f"与课程 L{row['id']:03d} 时间重叠，请调整时段。")


def _validate_times(start, end):
    a,b = datetime.fromisoformat(start),datetime.fromisoformat(end)
    if a.tzinfo or b.tzinfo or b <= a:
        raise ValueError('结束时间须晚于开始时间。')
    if start != a.isoformat(timespec='minutes') or end != b.isoformat(timespec='minutes'):
        raise ValueError('日期时间格式不正确。')


def create_lesson(sid, kind, start, end, location):
    _validate_times(start,end)
    if not location.strip():
        raise ValueError('请填写地点。')
    if kind not in ('私教','陪打'):
        raise ValueError('课程类型不正确。')
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        _conflict(connection,start,end)
        return connection.execute('INSERT INTO lessons(student_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?)',(sid,kind,start,end,location.strip())).lastrowid


def chinese_count(text):
    """Count Han characters, excluding punctuation, spaces, digits and Latin."""
    return sum(
        '\u3400' <= char <= '\u4dbf' or '\u4e00' <= char <= '\u9fff'
        or '\uf900' <= char <= '\ufaff' or 0x20000 <= ord(char) <= 0x2FA1F
        or 0x30000 <= ord(char) <= 0x323AF
        for char in (text or '')
    )


def validate_checkin(note, goal):
    errors = []
    for label, text in [('课后总结 / 训练记录', note), ('下次目标', goal)]:
        count = chinese_count(text)
        if count < 11:
            errors.append(f'{label}需要至少 11 个中文字符，目前 {count} 个。')
    if errors:
        raise ValueError('\n'.join(errors))
    return note.strip(), goal.strip()


def check_in(lid, note, goal):
    note, goal = validate_checkin(note, goal)
    with store.connect() as connection:
        if connection.execute('UPDATE lessons SET note=?,next_goal=?,checkin_done=1,checked_in_at=COALESCE(checked_in_at,?) WHERE id=?',(note.strip(),goal.strip(),now_london().isoformat(timespec='seconds'),lid)).rowcount != 1:
            raise ValueError('课程不存在。')


def cancel(lid, reason, advance, details=''):
    if reason not in ('学员取消','天气','教练原因','其他'):
        raise ValueError('请选择取消理由。')
    if not isinstance(advance,bool):
        raise ValueError('请选择是否提前至少 24 小时。')
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
        if not row or row['status'] in ('已取消','已改期'):
            raise ValueError('这节课已取消、已改期或不存在，请刷新。')
        saved_reason = reason + ('：'+details.strip() if details.strip() else '')
        connection.execute("UPDATE lessons SET status='已取消',reason=?,cancellation_24h=?,absence=? WHERE id=?",(saved_reason,int(advance),int(reason=='学员取消' and not advance),lid))


def reschedule(lid, target_day, start_time, end_time, location, reason=''):
    if not location.strip():
        raise ValueError('请填写新地点。')
    if end_time <= start_time:
        raise ValueError('结束时间须晚于开始时间。')
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        original = connection.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
        if not original or original['status'] == '已取消':
            raise ValueError('这节课已取消或不存在。')
        if original['status'] == '已改期' and not original['reschedule_pending']:
            raise ValueError('已生成新的预约，请对新预约操作。')
        if connection.execute('SELECT id FROM lessons WHERE original_lesson_id=?',(lid,)).fetchone():
            raise ValueError('已经生成改期课程，不会重复创建。')
        new_id = None
        if target_day is not None:
            if isinstance(target_day,str):
                target_day = date.fromisoformat(target_day)
            start = datetime.combine(target_day,start_time).isoformat(timespec='minutes')
            end = datetime.combine(target_day,end_time).isoformat(timespec='minutes')
            _validate_times(start,end)
            if (start,end,location.strip()) == (original['starts_at'],original['ends_at'],original['location']):
                raise ValueError('日期、时段和地点都未变化。')
            _conflict(connection,start,end,lid)
            new_id = connection.execute('INSERT INTO lessons(student_id,kind,starts_at,ends_at,location,original_lesson_id) VALUES(?,?,?,?,?,?)',(original['student_id'],original['kind'],start,end,location.strip(),lid)).lastrowid
        connection.execute("UPDATE lessons SET status='已改期',reschedule_pending=?,change_reason=?,proposed_start=?,proposed_end=?,proposed_location=? WHERE id=?",(int(target_day is None),reason.strip(),start_time.isoformat(timespec='minutes'),end_time.isoformat(timespec='minutes'),location.strip(),lid))
        return new_id


def save_daily_note(day, body):
    date.fromisoformat(day)
    with store.connect() as connection:
        connection.execute('INSERT INTO daily_notes(day,body,updated_at) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at',(day,body,now_london().isoformat(timespec='seconds')))


def daily_note(day):
    found = store.rows('SELECT body,updated_at FROM daily_notes WHERE day=?',(day,))
    return found[0] if found else {'body':'','updated_at':None}


def import_bookings(rows):
    from user_import_service import import_sheet_lessons
    return import_sheet_lessons(rows)
