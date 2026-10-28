"""Reuse sheet validation, with authenticated coach-scoped persistence."""
from slot_reader import clean_name
from user_import_service import _check
import ops_store as store
import ops_auth as auth
import ops_service as svc
import user_service as rules


def sheet_target(token,coach_id):
    with store.connect() as con:
        user=auth.require(con,token)
        coach_id=svc._coach(con,user,coach_id)
        if user['role']!='system_admin' and user['coach_id']!=coach_id:return False
        return bool(con.execute("SELECT 1 FROM accounts WHERE coach_id=? AND username='luo'",(coach_id,)).fetchone())


def process(token,rows,coach_id=None,write=False,template=None):
    if not isinstance(rows,list) or len(rows)>200:raise ValueError('一次最多导入 200 个时段。')
    with store.connect() as con:
        if write:con.execute('BEGIN IMMEDIATE')
        user=auth.require(con,token)
        coach_id=svc._coach(con,user,coach_id)
        if write:svc.require_course_writer(user,coach_id)
        if template=='xiaoluo' and not con.execute("SELECT 1 FROM accounts WHERE coach_id=? AND username='luo'",(coach_id,)).fetchone():
            raise auth.PermissionDenied('小罗区域只能导入 Luo 的课表，未保存。')
        scope,values=svc._student_scope(user)
        people=[dict(r) for r in con.execute('SELECT s.id,s.name FROM students s WHERE '+scope,values)]
        by_name={}
        for person in people:by_name.setdefault(clean_name(person['name']).casefold(),[]).append(person)
        existing=[dict(r) for r in con.execute('SELECT id,student_id,kind,starts_at,ends_at,location,status,archived FROM lessons WHERE coach_id=?',(coach_id,))]
        for row in existing:
            if row['archived']:row['status']='已取消'
        result=[];virtual=-1;planned={}
        for item in rows:
            row=dict(item);name=clean_name(row.get('name',''))
            if not name:
                result.append({**row,'result':'缺少姓名','new_student':False});continue
            matches=by_name.get(name.casefold(),[])
            if row.get('student_id') is not None:
                person=next((p for p in people if p['id']==row['student_id']),None)
                if person is None:
                    result.append({**row,'result':'学员不存在或不在权限范围内','new_student':False});continue
            elif len(matches)>1:
                result.append({**row,'result':'同名学员，请选择对应档案','new_student':False});continue
            elif matches:person=matches[0]
            else:
                person={'id':virtual,'name':name};virtual-=1;by_name[name.casefold()]=[person]
            row['student_id']=person['id']
            row['student_action']='自动建档' if person['id']<0 else f"已有学员 S{person['id']:03d}"
            check=_check(row,existing);created=False
            from ops_credits import enabled,available,booking_enabled
            scope_key=(person['id'],row['kind'])
            reserved=planned.get(scope_key,0) if not write and booking_enabled(con) else 0
            if check=='可以导入' and enabled(con) and (person['id']<0 or available(con,person['id'],coach_id,row['kind'])-reserved<1):
                check='未购买该教练的对应课程，或已用完；请 Jamie 先建档并录入课包'
            if check=='可以导入':
                if not write:planned[scope_key]=planned.get(scope_key,0)+1
                if write:
                    if person['id']<0:
                        person['id']=con.execute('INSERT INTO students(name,joined_at) VALUES(?,?)',(person['name'],rules.today_london().isoformat())).lastrowid
                        people.append(person);created=True
                        auth.audit(con,user['id'],'import_create_student','students',person['id'])
                    row['student_id']=person['id']
                    con.execute('INSERT OR IGNORE INTO student_coaches VALUES(?,?)',(person['id'],coach_id))
                    svc._new_lesson(con,user,person['id'],row['kind'],row['starts_at'],row['ends_at'],row['location'],coach_id)
                    check='已添加'
                existing.append({**row,'status':'待上课','location':row['location'].strip()})
            result.append({**row,'result':check,'new_student':created})
        return result
