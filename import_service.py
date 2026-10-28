"""Import appointments into the existing three-table model; never deduct credits."""
from datetime import datetime, timedelta

import database
from slot_reader import clean_name, COURT_COLORS


def hourly_slots(parsed):
    records = []
    for original in parsed['records']:
        if original['group_label']:
            continue
        start = datetime.fromisoformat(original['starts_at'])
        end = datetime.fromisoformat(original['ends_at'])
        hour_index = 0
        while start < end:
            next_hour = start + timedelta(hours=1)
            if next_hour > end:
                raise ValueError('有不足一小时的时段，需要人工处理。')
            row = dict(original)
            row['starts_at'] = start.isoformat(timespec='minutes')
            row['ends_at'] = next_hour.isoformat(timespec='minutes')
            if 'hour_colors' in original:
                color = original['hour_colors'][hour_index]
                row['fill_color'] = color
                row['location'] = COURT_COLORS.get(color, '')
            records.append(row)
            start = next_hour
            hour_index += 1
    return records


def _check(db, row, existing, trusted_sheet=False):
    sid = row.get('student_id')
    if not sid:
        return '未匹配学员，请先建档或选择已有学员'
    student = db.execute('SELECT id FROM students WHERE id=?', (sid,)).fetchone()
    if not student and not (trusted_sheet and isinstance(sid, int) and sid < 0):
        return '学员不存在，请重新选择'
    kind = row.get('kind')
    if kind not in ('私教', '陪打'):
        return '课程类型不正确'
    try:
        start = datetime.fromisoformat(row['starts_at'])
        end = datetime.fromisoformat(row['ends_at'])
        if start.tzinfo is not None or end.tzinfo is not None or end - start != timedelta(hours=1):
            return '时段必须为一小时'
        if row['starts_at'] != start.isoformat(timespec='minutes') or row['ends_at'] != end.isoformat(timespec='minutes'):
            return '时间格式不正确'
    except (ValueError, TypeError, KeyError):
        return '时间格式不正确'
    if not row.get('location', '').strip():
        return '请填写地点'
    for lesson in existing:
        if (lesson['student_id'], lesson['kind'], lesson['starts_at'], lesson['ends_at']) == (sid, kind, row['starts_at'], row['ends_at']):
            if lesson['location'] == row['location'].strip():
                return '重复课程，跳过（包括已完成或已取消的记录）'
            return '已有同一课程但地点不同，请在原记录中人工处理'
    for lesson in existing:
        if lesson['status'] != '已取消' and lesson['starts_at'] < row['ends_at'] and lesson['ends_at'] > row['starts_at']:
            return '与已有课程或本批其他时段重叠'
    if trusted_sheet:
        return '可以导入'
    balance = db.execute('SELECT COUNT(*) count,COALESCE(SUM(remaining),0) remaining FROM packages WHERE student_id=? AND kind=?', (sid, kind)).fetchone()
    if balance['count'] == 0:
        return f'没有购买{kind}课程'
    if balance['remaining'] < 1:
        return f'{kind}余额不足'
    return '可以导入'


def _simulate(row):
    return {**row, 'location': row['location'].strip(), 'status': '待上课'}


def preview_import(rows):
    results = []
    with database.connect() as db:
        existing = [dict(r) for r in db.execute('SELECT * FROM lessons')]
        for row in rows:
            reason = _check(db, row, existing)
            results.append({**row, 'result': reason})
            if reason == '可以导入':
                existing.append(_simulate(row))
    return results


def import_lessons(rows):
    if len(rows) > 200:
        raise ValueError('一次最多导入 200 个时段。')
    results = []
    with database.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = [dict(r) for r in db.execute('SELECT * FROM lessons')]
        for row in rows:
            reason = _check(db, row, existing)
            if reason == '可以导入':
                db.execute('INSERT INTO lessons(student_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?)', (row['student_id'], row['kind'], row['starts_at'], row['ends_at'], row['location'].strip()))
                existing.append(_simulate(row))
                reason = '已添加'
            results.append({**row, 'result': reason})
    return results


def _sheet_process(rows, write):
    """Trusted booked sheet: create missing names but never fabricate credits."""
    if len(rows) > 200:
        raise ValueError('一次最多导入 200 个时段。')
    results = []
    with database.connect() as db:
        if write:
            db.execute('BEGIN IMMEDIATE')
        people = [dict(p) for p in db.execute('SELECT id,name FROM students')]
        by_name = {}
        for person in people:
            by_name.setdefault(clean_name(person['name']).casefold(), []).append(person)
        virtual_id = -1
        existing = [dict(r) for r in db.execute('SELECT * FROM lessons')]
        for original in rows:
            row = dict(original)
            name = clean_name(row.get('name', ''))
            if not name:
                results.append({**row, 'result': '缺少学员称呼', 'new_student': False})
                continue
            normalized = name.casefold()
            candidates = by_name.get(normalized, [])
            override = row.get('student_id')
            if override is not None:
                matches = [p for p in people if p['id'] == override]
                if not matches:
                    results.append({**row, 'result': '所选学员不存在', 'new_student': False})
                    continue
                person = matches[0]
            elif len(candidates) > 1:
                results.append({**row, 'result': '存在同名学员，请在更多选项中选择', 'new_student': False})
                continue
            elif candidates:
                person = candidates[0]
            else:
                person = {'id': virtual_id, 'name': name}
                virtual_id -= 1
                by_name[normalized] = [person]
            row['student_id'] = person['id']
            row['student_action'] = '自动建档' if person['id'] < 0 else f"已有学员 S{person['id']:03d}"
            reason = _check(db, row, existing, trusted_sheet=True)
            created = False
            if reason == '可以导入':
                if write:
                    if person['id'] < 0:
                        person['id'] = db.execute('INSERT INTO students(name,remarks) VALUES(?,?)', (person['name'], '由课表导入；学员信息和购买课时待补录。')).lastrowid
                        people.append(person)
                        row['student_id'] = person['id']
                        created = True
                    db.execute('INSERT INTO lessons(student_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?)', (row['student_id'], row['kind'], row['starts_at'], row['ends_at'], row['location'].strip()))
                    reason = '已添加'
                existing.append(_simulate(row))
            results.append({**row, 'result': reason, 'new_student': created})
    return results


def preview_sheet_import(rows):
    return _sheet_process(rows, write=False)


def import_sheet_lessons(rows):
    return _sheet_process(rows, write=True)
