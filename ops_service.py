"""Role-scoped access to existing tables. No credit changes in phase 1."""
from datetime import date, datetime, timedelta
import hashlib
import json

import ops_store as store
import ops_auth as auth
import user_service as rules
import ops_reviews as workflow
import ops_credits as credits

# Preserve the original cumulative rule unless the user confirms a different scope.
ABSENCE_PER_COACH=False
COACH_CAN_EDIT_STUDENTS=True

ADMIN_ROLES=('operator','system_admin')
# Explicit allowlists: balances and internal payment flags never enter coach responses.
LESSON_COLUMNS='''l.id,l.student_id,l.coach_id,l.kind,l.starts_at,l.ends_at,l.location,
 l.status,l.note,l.next_goal,l.checkin_done,l.checked_in_at,l.reschedule_pending,
 l.reason,l.change_reason,l.original_lesson_id,l.proposed_start,l.proposed_end,l.proposed_location,
 l.revision,l.cancellation_24h,
 (SELECT f.state FROM feedback_submissions f WHERE f.lesson_id=l.id AND f.is_current=1) review_state,
 (SELECT f.review_comment FROM feedback_submissions f WHERE f.lesson_id=l.id AND f.is_current=1) review_comment,
 (SELECT a.display_name FROM feedback_submissions f JOIN accounts a ON a.id=f.reviewed_by WHERE f.lesson_id=l.id AND f.is_current=1) reviewed_by_name,
 (SELECT x.starts_at FROM lessons x WHERE x.original_lesson_id=l.id AND x.coach_id=l.coach_id AND x.archived=0 LIMIT 1) moved_to,
 s.name student_name,c.name coach_name'''
STUDENT_COLUMNS='''s.id,s.name,s.contact,s.source,s.joined_at,s.revision'''


def list_lessons(token, week=None, student_id=None, coach_id=None):
    with store.connect() as con:
        user=auth.require(con,token)
        coach_id=_coach(con,user,coach_id,required=False)
        where,values=['l.archived=0'],[]
        if coach_id is not None:
            where.append('l.coach_id=?');values.append(coach_id)
        if week:
            if isinstance(week,str):week=date.fromisoformat(week)
            where.append('l.starts_at>=? AND l.starts_at<?')
            values.extend([week.isoformat(),(week+timedelta(days=7)).isoformat()])
        if student_id is not None:
            where.append('l.student_id=?');values.append(student_id)
        sql=f'SELECT {LESSON_COLUMNS} FROM lessons l JOIN students s ON s.id=l.student_id LEFT JOIN coaches c ON c.id=l.coach_id'
        if where:sql+=' WHERE '+' AND '.join(where)
        result=[dict(r) for r in con.execute(sql+' ORDER BY l.starts_at,l.id',values)]
        if credits.calendar_enabled(con):
            confirmed={r['id']:r['attendance_confirmed'] for r in con.execute('SELECT id,attendance_confirmed FROM lessons')}
            for row in result:row.update(manual_attendance=True,attendance_confirmed=confirmed[row['id']])
        for row in result:row['absence_count']=_absence(con,row['student_id'],row['coach_id'])
        return result


def _student_scope(user):
    if user['role']!='coach':return '1=1',[]
    return '''(EXISTS(SELECT 1 FROM student_coaches sc WHERE sc.student_id=s.id AND sc.coach_id=?)
     OR EXISTS(SELECT 1 FROM lessons l WHERE l.student_id=s.id AND l.coach_id=?))''',[user['coach_id'],user['coach_id']]


def list_students(token, search='',coach_id=None):
    with store.connect() as con:
        user=auth.require(con,token)
        coach_id=_coach(con,user,coach_id,required=False)
        scope,values=_student_scope(user)
        rows=[dict(r) for r in con.execute(f'SELECT {STUDENT_COLUMNS} FROM students s WHERE '+scope+' ORDER BY s.name,s.id',values)]
        if coach_id is not None:
            related={r[0] for r in con.execute('''SELECT student_id FROM student_coaches WHERE coach_id=?
              UNION SELECT student_id FROM packages WHERE coach_id=?
              UNION SELECT student_id FROM lessons WHERE coach_id=? AND archived=0''',(coach_id,coach_id,coach_id))}
            rows=[r for r in rows if r['id'] in related]
        for row in rows:
            pref=con.execute('SELECT starred FROM student_preferences WHERE account_id=? AND student_id=?',(user['id'],row['id'])).fetchone()
            row['starred']=pref[0] if pref else 0
            row['absence_count']=_absence(con,row['id'],user['coach_id'])
            _add_teaching(con,user,row,coach_id)
        rows.sort(key=lambda p:(not p['starred'],rules._initials(p['name']),p['id']))
        keyword=search.strip().casefold()
        return [r for r in rows if not keyword or keyword in r['name'].casefold() or keyword in f"S{r['id']:03d}".casefold()]


def student_detail(token, sid,coach_id=None):
    with store.connect() as con:
        user=auth.require(con,token)
        coach_id=_coach(con,user,coach_id,required=False)
        scope,values=_student_scope(user)
        row=con.execute(f'SELECT {STUDENT_COLUMNS} FROM students s WHERE s.id=? AND '+scope,[sid]+values).fetchone()
        if not row:raise auth.PermissionDenied('该档案不在你的可见范围内。')
        result=dict(row)
        pref=con.execute('SELECT starred FROM student_preferences WHERE account_id=? AND student_id=?',(user['id'],sid)).fetchone()
        result['starred']=pref[0] if pref else 0
        result['absence_count']=_absence(con,sid,user['coach_id'])
        _add_teaching(con,user,result,coach_id)
        return result


def link_student(token, sid, coach_id):
    """Explicit relationship; access is never inferred from a matching name."""
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user=auth.require(con,token,ADMIN_ROLES)
        if not con.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone():raise ValueError('学员不存在。')
        if not con.execute('SELECT 1 FROM coaches WHERE id=?',(coach_id,)).fetchone():raise ValueError('教练不存在。')
        added=con.execute('INSERT OR IGNORE INTO student_coaches VALUES(?,?)',(sid,coach_id)).rowcount
        if added:auth.audit(con,user['id'],'link_student','students',sid,{'coach_id':coach_id})


def assign_legacy_lesson(token, lid, coach_id):
    """Optional explicit attribution, not called automatically during migration."""
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user=auth.require(con,token,('system_admin',))
        if not con.execute('SELECT 1 FROM coaches WHERE id=?',(coach_id,)).fetchone():raise ValueError('教练不存在。')
        row=con.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
        if not row:raise ValueError('课程不存在。')
        if row['coach_id'] is not None:raise ValueError('课程已有归属，不执行覆盖。')
        if row['package_id']:
            pkg=con.execute('SELECT coach_id FROM packages WHERE id=?',(row['package_id'],)).fetchone()
            if pkg and pkg['coach_id'] is not None and pkg['coach_id']!=coach_id:
                raise ValueError('关联课包属于其他教练，请先人工核对。')
        con.execute('UPDATE lessons SET coach_id=? WHERE id=?',(coach_id,lid))
        con.execute('INSERT OR IGNORE INTO student_coaches VALUES(?,?)',(row['student_id'],coach_id))
        auth.audit(con,user['id'],'assign_legacy_lesson','lessons',lid,{'coach_id':coach_id})


def admin_overview(token):
    with store.connect() as con:
        auth.require(con,token,ADMIN_ROLES)
        return dict(con.execute('''SELECT
        (SELECT count(*) FROM students) students,
        (SELECT count(*) FROM lessons WHERE coach_id IS NULL) unassigned_lessons,
        (SELECT count(*) FROM coaches) coaches''').fetchone())


def audit_log(token):
    with store.connect() as con:
        auth.require(con,token,('system_admin',))
        return [dict(r) for r in con.execute('''SELECT o.id,o.action,o.entity,o.entity_id,o.details,o.created_at,
          a.display_name actor FROM ops_audit o LEFT JOIN accounts a ON a.id=o.actor_id ORDER BY o.id DESC LIMIT 200''')]


class StaleRecord(ValueError):pass


def _coach(con,user,coach_id=None,required=True):
    if user['role']=='coach':
        if coach_id is not None and coach_id!=user['coach_id']:raise auth.PermissionDenied('不能操作其他教练的课程。')
        coach_id=user['coach_id']
    if coach_id is None:
        if required:raise ValueError('请先选择所属教练。')
        return None
    if not con.execute('SELECT 1 FROM coaches WHERE id=?',(coach_id,)).fetchone():raise ValueError('教练不存在。')
    return coach_id


def coaches(token):
    with store.connect() as con:
        user=auth.require(con,token)
        rows=[dict(r) for r in con.execute('SELECT id,name FROM coaches ORDER BY id')]
        return [r for r in rows if user['role']!='coach' or r['id']==user['coach_id']]


def _absence(con,sid,coach_id):
    extra=' AND coach_id=?' if ABSENCE_PER_COACH and coach_id is not None else ''
    values=[sid]+([coach_id] if extra else [])
    return con.execute("SELECT COALESCE(SUM(absence),0) FROM lessons WHERE student_id=? AND archived=0 AND kind IN ('私教','陪打')"+extra,values).fetchone()[0]


def _student(con,user,sid):
    scope,values=_student_scope(user)
    row=con.execute('SELECT s.* FROM students s WHERE s.id=? AND '+scope,[sid]+values).fetchone()
    if not row:raise auth.PermissionDenied('该学员不在你的可见范围内。')
    return dict(row)


def _lesson(con,user,lid,revision=None):
    row=con.execute('SELECT * FROM lessons WHERE id=? AND archived=0',(lid,)).fetchone()
    if not row or (user['role']=='coach' and row['coach_id']!=user['coach_id']):
        raise auth.PermissionDenied('课程不存在或不在你的操作范围内。')
    if row['coach_id'] is None:raise ValueError('课程尚未确定归属，请先由系统管理员核对。')
    if revision is not None and row['revision']!=revision:
        raise StaleRecord('记录已被他人修改。请点击重新载入，再核对内容后保存。')
    return dict(row)


def require_course_writer(user,coach_id):
    if user['role']!='system_admin' and (coach_id is None or user['coach_id']!=coach_id):
        raise auth.PermissionDenied('其他教练的课程仅可查看，不能新增、修改或删除。')


def _writable_lesson(con,user,lid,revision):
    row=_lesson(con,user,lid,revision)
    require_course_writer(user,row['coach_id'])
    return row


def lesson_detail(token,lid):
    with store.connect() as con:
        user=auth.require(con,token)
        _lesson(con,user,lid)
    rows=list_lessons(token)
    return next(row for row in rows if row['id']==lid)


def _revision(value):
    if type(value) is not int or value<0:raise ValueError('缺少有效的记录版本，请重新载入。')
    return value


def _conflict(con,coach_id,start,end,excluded=-1):
    if con.execute("SELECT 1 FROM lessons WHERE coach_id=? AND archived=0 AND status NOT IN ('已取消','已改期') AND starts_at<? AND ends_at>? AND id!=?",(coach_id,end,start,excluded)).fetchone():
        raise ValueError('与该教练已有课程时间重叠，请调整时段。')
    if credits.booking_enabled(con) and con.execute('SELECT 1 FROM group_reminders WHERE coach_id=? AND archived=0 AND starts_at<? AND ends_at>?',(coach_id,end,start)).fetchone():
        raise ValueError('与该教练已有团课时间重叠，请调整时段。')


def _new_lesson(con,user,sid,kind,start,end,location,coach_id,original=None):
    require_course_writer(user,coach_id)
    rules._validate_times(start,end)
    if kind not in ('私教','陪打'):raise ValueError('课程类型不正确。')
    if not location.strip():raise ValueError('请填写地点。')
    _student(con,user,sid)
    credits.require_balance(con,sid,coach_id,kind)
    _conflict(con,coach_id,start,end)
    lid=con.execute('INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location,original_lesson_id) VALUES(?,?,?,?,?,?,?)',
        (sid,coach_id,kind,start,end,location.strip(),original)).lastrowid
    con.execute('INSERT OR IGNORE INTO student_coaches VALUES(?,?)',(sid,coach_id))
    auth.audit(con,user['id'],'create_lesson','lessons',lid,{'coach_id':coach_id,'original_lesson_id':original})
    return lid


def create_lesson(token,sid,kind,start,end,location,coach_id=None,request_key=None):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        coach_id=_coach(con,user,coach_id)
        require_course_writer(user,coach_id)
        payload=json.dumps([sid,kind,start,end,location.strip(),coach_id],ensure_ascii=False)
        fingerprint=hashlib.sha256(payload.encode()).hexdigest()
        if not request_key or not 10<=len(request_key)<=100:raise ValueError('缺少提交标识，请重新打开表单。')
        cached=con.execute('SELECT * FROM ops_requests WHERE account_id=? AND request_key=?',(user['id'],request_key)).fetchone()
        if cached:
            if cached['fingerprint']!=fingerprint:raise ValueError('提交标识已用于其他内容，请重新打开表单。')
            _lesson(con,user,cached['lesson_id'])
            return cached['lesson_id']
        lid=_new_lesson(con,user,sid,kind,start,end,location,coach_id)
        con.execute('INSERT INTO ops_requests VALUES(?,?,?,?)',(user['id'],request_key,fingerprint,lid))
        return lid


def check_in(token,lid,note,goal,expected_revision):
    note,goal=rules.validate_checkin(note,goal)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(expected_revision))
        if row['status'] in ('已取消','已改期'):raise ValueError('取消或改期的原课程不能打卡。')
        if credits.calendar_enabled(con):
            if datetime.fromisoformat(row['starts_at'])>rules.now_london().replace(tzinfo=None):raise ValueError('课程尚未开始，不能确认上课或打卡。')
            con.execute('UPDATE lessons SET attendance_confirmed=1 WHERE id=?',(lid,))
        workflow.before_feedback_change(con,lid)
        keep_completed = credits.enabled(con) and workflow.approved(con,lid)
        con.execute("UPDATE lessons SET note=?,next_goal=?,checkin_done=1,checked_in_at=COALESCE(checked_in_at,?),status=CASE WHEN status='已完成' THEN '待上课' ELSE status END,revision=revision+1 WHERE id=?",
            (note,goal,rules.now_london().isoformat(timespec='seconds'),lid))
        if keep_completed:con.execute("UPDATE lessons SET status='已完成' WHERE id=?",(lid,))
        submission_id=workflow.submit_current(con,lid,user['id'])
        auth.audit(con,user['id'],'save_feedback','lessons',lid,{'coach_id':row['coach_id'],'previous_revision':row['revision'],'submission_id':submission_id,'after_approval_edit':bool(keep_completed)})


def cancel(token,lid,reason,advance,detail,expected_revision):
    if reason not in ('学员取消','天气','教练原因','其他') or type(advance) is not bool:raise ValueError('取消选项不正确。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(expected_revision))
        if row['status'] in ('已取消','已改期'):raise ValueError('该课程已经取消或改期。')
        if credits.enabled(con) and (row['charged'] or workflow.approved(con,lid)):
            raise ValueError('已确认完成或已扣课的课程不能直接取消，请交由 Jamie 核对。')
        combined=reason+('：'+detail.strip() if detail.strip() else '')
        con.execute("UPDATE lessons SET status='已取消',reason=?,cancellation_24h=?,absence=?,revision=revision+1 WHERE id=?",
            (combined,int(advance),int(reason=='学员取消' and not advance),lid))
        workflow.invalidate(con,lid,'课程取消')
        if credits.enabled(con) and reason=='学员取消' and not advance and _absence(con,row['student_id'],None)>=2:
            con.execute('UPDATE lessons SET charge_due=1 WHERE id=?',(lid,))
            if credits.booking_enabled(con):credits.charge(con,user,lid,'absence')
        auth.audit(con,user['id'],'cancel_lesson','lessons',lid,{'reason':combined,'at_least_24h':advance})


def confirm_attendance(token,lid,revision):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(revision))
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        if row['status'] in ('已取消','已改期','已完成') or row['attendance_confirmed']:raise ValueError('课程状态已变化，请刷新。')
        if datetime.fromisoformat(row['starts_at'])>rules.now_london().replace(tzinfo=None):raise ValueError('课程尚未开始。')
        con.execute('UPDATE lessons SET attendance_confirmed=1,revision=revision+1 WHERE id=?',(lid,))
        auth.audit(con,user['id'],'confirm_attendance','lessons',lid)


def reschedule(token,lid,day,begin,end,location,reason,expected_revision):
    if not location.strip():raise ValueError('请填写地点。')
    if end<=begin:raise ValueError('结束时间须晚于开始时间。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(expected_revision))
        if credits.enabled(con) and (row['charged'] or workflow.approved(con,lid)):
            raise ValueError('已确认完成或已扣课的课程不能直接改期，请交由 Jamie 核对。')
        if row['status']=='已取消' or (row['status']=='已改期' and not row['reschedule_pending']):raise ValueError('原课程已取消或已生成改期预约。')
        if con.execute('SELECT 1 FROM lessons WHERE original_lesson_id=?',(lid,)).fetchone():raise ValueError('已经生成改期课程。')
        new_id=None
        if day is not None:
            if isinstance(day,str):day=date.fromisoformat(day)
            start=datetime.combine(day,begin).isoformat(timespec='minutes')
            finish=datetime.combine(day,end).isoformat(timespec='minutes')
            if (start,finish,location.strip())==(row['starts_at'],row['ends_at'],row['location']):raise ValueError('日期、时段和地点都未变化。')
            # Original slot is released atomically before checking the new slot.
            con.execute("UPDATE lessons SET status='已改期',reschedule_pending=0 WHERE id=?",(lid,))
            new_id=_new_lesson(con,user,row['student_id'],row['kind'],start,finish,location,row['coach_id'],lid)
        con.execute("UPDATE lessons SET status='已改期',reschedule_pending=?,change_reason=?,proposed_start=?,proposed_end=?,proposed_location=?,revision=revision+1 WHERE id=?",
            (int(day is None),reason.strip(),begin.isoformat(timespec='minutes'),end.isoformat(timespec='minutes'),location.strip(),lid))
        workflow.invalidate(con,lid,'课程改期')
        auth.audit(con,user['id'],'reschedule_lesson','lessons',lid,{'new_lesson_id':new_id,'pending':day is None})
        return new_id


def archive_lesson(token,lid,expected_revision):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(expected_revision))
        if row['status']!='待上课' or row['checkin_done'] or row['charged'] or row['charge_due'] or row['original_lesson_id']:
            raise ValueError('只能移除误添加且未打卡、未结算、未改期的普通课程。')
        if con.execute('SELECT 1 FROM lessons WHERE original_lesson_id=?',(lid,)).fetchone():raise ValueError('该课程已有改期关联。')
        con.execute('UPDATE lessons SET archived=1,revision=revision+1 WHERE id=?',(lid,))
        workflow.invalidate(con,lid,'预约移除')
        auth.audit(con,user['id'],'archive_mistaken_lesson','lessons',lid)


def clear_feedback(token,lid,expected_revision):
    """Remove an unreviewed feedback submission, preserving the course and audit."""
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        row=_writable_lesson(con,user,lid,_revision(expected_revision))
        if row['charged'] or row['charge_due'] or workflow.approved(con,lid):
            raise ValueError('这节课已有结算标记，请交由系统管理员处理，不能直接删除反馈。')
        workflow.before_feedback_change(con,lid)
        workflow.invalidate(con,lid,'教练删除反馈')
        con.execute("UPDATE lessons SET note='',next_goal='',checkin_done=0,checked_in_at=NULL,status=CASE WHEN status='已完成' THEN '待上课' ELSE status END,revision=revision+1 WHERE id=?",(lid,))
        auth.audit(con,user['id'],'clear_own_feedback','lessons',lid,{'coach_id':row['coach_id'],'previous_revision':row['revision'],
            'previous_feedback':{'note':row['note'],'next_goal':row['next_goal'],'checked_in_at':row['checked_in_at']}})


def teaching_access(user,coach_id):
    return coach_id is not None and (user['role']=='system_admin' or user['coach_id']==coach_id)


def _add_teaching(con,user,row,coach_id):
    target=coach_id if coach_id is not None else user['coach_id']
    if not teaching_access(user,target):return
    if not con.execute('SELECT 1 FROM student_coaches WHERE student_id=? AND coach_id=?',(row['id'],target)).fetchone() and not con.execute('SELECT 1 FROM lessons WHERE student_id=? AND coach_id=?',(row['id'],target)).fetchone():return
    private=con.execute('SELECT level,pain_points,goals,remarks,revision teaching_revision FROM coach_student_profiles WHERE student_id=? AND coach_id=?',(row['id'],target)).fetchone()
    row.update(dict(private) if private else dict(level='',pain_points='',goals='',remarks='',teaching_revision=-1))


def save_student(token,name,contact='',source='',level='',pain_points='',goals='',remarks='',joined_at=None,sid=None,coach_id=None,expected_revision=None,teaching_revision=-1):
    if not name.strip():raise ValueError('请填写姓名。')
    if joined_at:date.fromisoformat(joined_at)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        if user['role']=='coach' and not COACH_CAN_EDIT_STUDENTS:raise auth.PermissionDenied('基础档案由 Jamie 管理。')
        coach_id=_coach(con,user,coach_id,required=False)
        own_profile=teaching_access(user,coach_id)
        if not own_profile and any((level,pain_points,goals,remarks)):raise auth.PermissionDenied('不能编辑其他教练的私有教学档案。')
        values=tuple(s.strip() for s in (name,contact,source))
        if sid is None:
            coach_id=_coach(con,user,coach_id)
            sid=con.execute('INSERT INTO students(name,contact,source,joined_at) VALUES(?,?,?,?)',values+(joined_at or rules.today_london().isoformat(),)).lastrowid
            con.execute('INSERT INTO student_coaches VALUES(?,?)',(sid,coach_id))
            action='create_student'
        else:
            row=_student(con,user,sid)
            if row['revision']!=_revision(expected_revision):raise StaleRecord('档案已被他人修改。请重新载入后再保存。')
            con.execute('UPDATE students SET name=?,contact=?,source=?,joined_at=?,revision=revision+1 WHERE id=?',values+(joined_at,sid))
            action='edit_shared_profile'
        if own_profile:
            if not con.execute('SELECT 1 FROM student_coaches WHERE student_id=? AND coach_id=?',(sid,coach_id)).fetchone():
                if not con.execute('SELECT 1 FROM lessons WHERE student_id=? AND coach_id=?',(sid,coach_id)).fetchone():raise auth.PermissionDenied('请先将学员明确关联到这位教练。')
                con.execute('INSERT INTO student_coaches VALUES(?,?)',(sid,coach_id))
            profile=con.execute('SELECT revision FROM coach_student_profiles WHERE student_id=? AND coach_id=?',(sid,coach_id)).fetchone()
            current=profile[0] if profile else -1
            if current!=teaching_revision:raise StaleRecord('私有教学档案已被修改，请重新载入。')
            con.execute('INSERT INTO coach_student_profiles VALUES(?,?,?,?,?,?,?) ON CONFLICT(student_id,coach_id) DO UPDATE SET level=excluded.level,pain_points=excluded.pain_points,goals=excluded.goals,remarks=excluded.remarks,revision=excluded.revision',
                (sid,coach_id,level.strip(),pain_points.strip(),goals.strip(),remarks.strip(),current+1))
        auth.audit(con,user['id'],action,'students',sid)
        return sid


def set_star(token,sid,starred):
    if type(starred) is not bool:raise ValueError('星标选项不正确。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token);_student(con,user,sid)
        con.execute('INSERT INTO student_preferences VALUES(?,?,?) ON CONFLICT(account_id,student_id) DO UPDATE SET starred=excluded.starred',(user['id'],sid,int(starred)))
        auth.audit(con,user['id'],'set_personal_star','students',sid,{'starred':starred})


def daily_note(token,day):
    date.fromisoformat(day)
    with store.connect() as con:
        user=auth.require(con,token)
        row=con.execute('SELECT body,updated_at,revision FROM account_notes WHERE account_id=? AND day=?',(user['id'],day)).fetchone()
        return dict(row) if row else {'body':'','updated_at':None,'revision':-1}


def save_daily_note(token,day,body,expected_revision):
    if date.fromisoformat(day)>rules.today_london():raise ValueError('不能保存未来日期的随手记。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token)
        current=con.execute('SELECT revision FROM account_notes WHERE account_id=? AND day=?',(user['id'],day)).fetchone()
        actual=current[0] if current else -1
        if actual!=expected_revision:raise StaleRecord('随手记已在其他窗口修改，请重新载入后再编辑。')
        con.execute('INSERT INTO account_notes VALUES(?,?,?,?,?) ON CONFLICT(account_id,day) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at,revision=excluded.revision',
            (user['id'],day,body,rules.now_london().isoformat(timespec='seconds'),actual+1))
        auth.audit(con,user['id'],'save_personal_note','account_notes',None,{'day':day})
