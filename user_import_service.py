"""Trusted sheet appointments: no payment or balance access."""
from datetime import datetime, timedelta

import user_store as store
from slot_reader import clean_name, COURT_COLORS


def hourly_slots(parsed):
    rows = []
    for original in parsed['records']:
        if original['group_label']:
            continue
        start = datetime.fromisoformat(original['starts_at'])
        end = datetime.fromisoformat(original['ends_at'])
        index = 0
        while start < end:
            following = start + timedelta(hours=1)
            if following > end:
                raise ValueError('有不足一小时的时段，请人工核对。')
            color = original.get('hour_colors',[original.get('fill_color','')])[index]
            rows.append({**original,'starts_at':start.isoformat(timespec='minutes'),'ends_at':following.isoformat(timespec='minutes'),'location':COURT_COLORS.get(color,''),'fill_color':color})
            start = following
            index += 1
    return rows


def _check(row, existing):
    if row.get('kind') not in ('私教','陪打'):
        return '课程类型不正确'
    try:
        start,end = datetime.fromisoformat(row['starts_at']),datetime.fromisoformat(row['ends_at'])
        if start.tzinfo or end.tzinfo or end-start != timedelta(hours=1):
            return '时段须为一小时'
    except (ValueError,KeyError,TypeError):
        return '时间格式不正确'
    if not row.get('location','').strip():
        return '请确认地点'
    for lesson in existing:
        if (lesson['student_id'],lesson['kind'],lesson['starts_at'],lesson['ends_at']) == (row['student_id'],row['kind'],row['starts_at'],row['ends_at']):
            return '重复课程，跳过' if lesson['location']==row['location'].strip() else '原课程地点不同，请人工核对'
    if any(l['status'] not in ('已取消','已改期') and l['starts_at']<row['ends_at'] and l['ends_at']>row['starts_at'] for l in existing):
        return '与其他课程时间重叠'
    return '可以导入'


def _process(rows, write=False):
    from user_service import today_london
    if len(rows)>200:
        raise ValueError('一次最多导入 200 个时段。')
    result=[]
    with store.connect() as connection:
        if write:
            connection.execute('BEGIN IMMEDIATE')
        people=[dict(p) for p in connection.execute('SELECT id,name FROM students')]
        by_name={}
        for p in people:
            by_name.setdefault(clean_name(p['name']).casefold(),[]).append(p)
        existing=[dict(l) for l in connection.execute('SELECT id,student_id,kind,starts_at,ends_at,location,status FROM lessons')]
        virtual=-1
        for item in rows:
            row=dict(item)
            name=clean_name(row.get('name',''))
            if not name:
                result.append({**row,'result':'缺少姓名','new_student':False})
                continue
            candidates=by_name.get(name.casefold(),[])
            override=row.get('student_id')
            if override is not None:
                selected=next((p for p in people if p['id']==override),None)
                if not selected:
                    result.append({**row,'result':'所选学员不存在','new_student':False})
                    continue
            elif len(candidates)>1:
                result.append({**row,'result':'同名学员，请选择对应档案','new_student':False})
                continue
            elif candidates:
                selected=candidates[0]
            else:
                selected={'id':virtual,'name':name}
                virtual-=1
                by_name[name.casefold()]=[selected]
            row['student_id']=selected['id']
            row['student_action']='自动建档' if selected['id']<0 else f"已有学员 S{selected['id']:03d}"
            check=_check(row,existing)
            created=False
            if check=='可以导入':
                if write:
                    if selected['id']<0:
                        selected['id']=connection.execute('INSERT INTO students(name,remarks,joined_at) VALUES(?,?,?)',(selected['name'],'由课表导入，基本信息待补充。',today_london().isoformat())).lastrowid
                        people.append(selected)
                        row['student_id']=selected['id']
                        created=True
                    connection.execute('INSERT INTO lessons(student_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?)',(row['student_id'],row['kind'],row['starts_at'],row['ends_at'],row['location'].strip()))
                    check='已添加'
                existing.append({**row,'status':'待上课','location':row['location'].strip()})
            result.append({**row,'result':check,'new_student':created})
    return result


def preview_sheet_import(rows):
    return _process(rows)


def import_sheet_lessons(rows):
    return _process(rows,True)
