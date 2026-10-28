"""Transactional publication and booking, with derived credit reservations."""
from datetime import datetime
import ops_auth as auth
import ops_store as store
import ops_service as svc
import ops_credits as credits
import student_auth
import user_service as rules


def now():return rules.now_london().replace(tzinfo=None).isoformat(timespec='minutes')


def _enabled(con):
    if not credits.booking_enabled(con):raise ValueError('请先关闭网站，运行“升级预约版.bat”。')


def _overlap_student(con,sid,start,end):
    if con.execute("SELECT 1 FROM lessons WHERE student_id=? AND archived=0 AND status NOT IN ('已取消','已改期') AND starts_at<? AND ends_at>?",(sid,end,start)).fetchone():
        raise ValueError('与你已有课程时间重叠。')


EXTERNAL_COACHES=('Zoey','小刘','小王')


def _available_coach(con,cid):
    accounts=con.execute('SELECT active FROM accounts WHERE coach_id=?',(cid,)).fetchall()
    if accounts:return any(r['active'] for r in accounts)
    row=con.execute('SELECT name FROM coaches WHERE id=?',(cid,)).fetchone()
    return bool(row and row['name'] in EXTERNAL_COACHES)


def _slot_writer(con,user,cid):
    row=con.execute('SELECT name FROM coaches WHERE id=?',(cid,)).fetchone()
    external=row and row['name'] in EXTERNAL_COACHES and not con.execute('SELECT 1 FROM accounts WHERE coach_id=?',(cid,)).fetchone()
    if user['role']=='operator' and external:return
    svc.require_course_writer(user,cid)


def slot_editable(token,cid):
    with store.connect() as con:
        user=auth.require(con,token)
        try:_slot_writer(con,user,cid)
        except auth.PermissionDenied:return False
        return True


def publish(token,kind,start,end,location,coach_id=None,external_name=None):
    rules._validate_times(start,end)
    if kind not in ('私教','陪打'):raise ValueError('仅支持私教和陪打时段。')
    if not isinstance(location,str) or not location.strip():raise ValueError('请填写地点。')
    # Require the same canonical local-minute format as existing lesson forms.
    if any(datetime.fromisoformat(t).tzinfo is not None or datetime.fromisoformat(t).isoformat(timespec='minutes')!=t for t in (start,end)):
        raise ValueError('请使用伦敦当地日期和时间，精确到分钟。')
    for value in (start,end):
        local=datetime.fromisoformat(value)
        if local.replace(tzinfo=rules.LONDON,fold=0).utcoffset()!=local.replace(tzinfo=rules.LONDON,fold=1).utcoffset():
            raise ValueError('夏令时切换时该时间重复或不存在，请选择其他时段。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token);_enabled(con)
        if external_name is not None:
            if user['role'] not in credits.ROLES:raise auth.PermissionDenied('只有Jamie或系统管理员可代录无账号教练的空闲时间。')
            if external_name not in EXTERNAL_COACHES:raise ValueError('不支持这位无账号教练。')
            if coach_id is not None:raise ValueError('请只选择一位教练。')
            found=con.execute('SELECT id FROM coaches WHERE name=? ORDER BY id',(external_name,)).fetchall()
            if len(found)>1:raise ValueError('教练重名，请管理员核对。')
            cid=found[0]['id'] if found else con.execute('INSERT INTO coaches(name) VALUES(?)',(external_name,)).lastrowid
            if not found:auth.audit(con,user['id'],'create_scheduling_coach','coaches',cid)
        else:cid=svc._coach(con,user,coach_id)
        _slot_writer(con,user,cid)
        if start<=now():raise ValueError('只能发布未来时段。')
        svc._conflict(con,cid,start,end)
        if con.execute('SELECT 1 FROM booking_slots WHERE coach_id=? AND closed=0 AND starts_at<? AND ends_at>?',(cid,end,start)).fetchone():
            raise ValueError('与已发布的可预约时段重叠。')
        slot=con.execute('INSERT INTO booking_slots(coach_id,kind,starts_at,ends_at,location) VALUES(?,?,?,?,?)',(cid,kind,start,end,location.strip())).lastrowid
        auth.audit(con,user['id'],'save_draft_slot' if credits.calendar_enabled(con) else 'publish_booking_slot','booking_slots',slot)
        return slot


def close_slot(token,slot_id,revision):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token);_enabled(con)
        row=con.execute('SELECT * FROM booking_slots WHERE id=?',(slot_id,)).fetchone()
        if not row:raise ValueError('时段不存在。')
        _slot_writer(con,user,row['coach_id'])
        if row['revision']!=revision:raise ValueError('时段已变化，请刷新。')
        con.execute('UPDATE booking_slots SET closed=1,revision=revision+1 WHERE id=?',(slot_id,))
        auth.audit(con,user['id'],'close_booking_slot','booking_slots',slot_id)


def staff_slots(token):
    with store.connect() as con:
        user=auth.require(con,token);_enabled(con)
        # Jamie can inspect all slots but only modify her own.
        where=' AND b.coach_id=?' if user['role']=='coach' else ''
        args=[now()]+([user['coach_id']] if where else [])
        return [dict(r) for r in con.execute('''SELECT b.*,c.name coach_name,
          EXISTS(SELECT 1 FROM lessons l WHERE l.coach_id=b.coach_id AND l.archived=0
            AND l.status NOT IN ('已取消','已改期') AND l.starts_at<b.ends_at AND l.ends_at>b.starts_at) booked
          FROM booking_slots b JOIN coaches c ON c.id=b.coach_id WHERE b.starts_at>?'''+where+' ORDER BY b.starts_at,b.id',args)]


def my_credits(token):
    with store.connect() as con:
        student=student_auth.require(con,token);sid=student['student_id']
        rows=con.execute('SELECT DISTINCT p.coach_id,c.name coach_name,p.kind FROM packages p JOIN coaches c ON c.id=p.coach_id WHERE p.student_id=? ORDER BY c.name,p.kind',(sid,)).fetchall()
        return [dict(r, balance=credits.balance(con,sid,r['coach_id'],r['kind']),occupied=credits.occupied(con,sid,r['coach_id'],r['kind']),available=credits.available(con,sid,r['coach_id'],r['kind'])) for r in rows]


def open_slots(token):
    with store.connect() as con:
        student=student_auth.require(con,token);sid=student['student_id']
        publication=' AND b.published=1' if credits.calendar_enabled(con) else ''
        rows=con.execute(f'''SELECT b.id,b.coach_id,c.name coach_name,b.kind,b.starts_at,b.ends_at,b.location,b.revision
          FROM booking_slots b JOIN coaches c ON c.id=b.coach_id WHERE b.closed=0 AND b.starts_at>? {publication}
          AND EXISTS(SELECT 1 FROM student_coaches sc WHERE sc.student_id=? AND sc.coach_id=b.coach_id)
          AND NOT EXISTS(SELECT 1 FROM lessons l WHERE l.coach_id=b.coach_id AND l.archived=0 AND l.status NOT IN ('已取消','已改期') AND l.starts_at<b.ends_at AND l.ends_at>b.starts_at)
          AND NOT EXISTS(SELECT 1 FROM group_reminders g WHERE g.coach_id=b.coach_id AND g.archived=0 AND g.starts_at<b.ends_at AND g.ends_at>b.starts_at)
          ORDER BY b.starts_at,b.id''',(now(),sid)).fetchall()
        return [dict(r,available=credits.available(con,sid,r['coach_id'],r['kind'])) for r in rows if _available_coach(con,r['coach_id'])]


def book(token,slot_id,revision,request_key):
    if not isinstance(request_key,str) or not 10<=len(request_key)<=100:raise ValueError('缺少预约标识，请重新打开页面。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');student=student_auth.require(con,token);sid=student['student_id']
        cached=con.execute('SELECT * FROM student_booking_requests WHERE student_id=? AND request_key=?',(sid,request_key)).fetchone()
        if cached:
            if cached['slot_id']!=slot_id:raise ValueError('预约标识已用于其他时段，请刷新。')
            return cached['lesson_id']
        row=con.execute('SELECT * FROM booking_slots WHERE id=?',(slot_id,)).fetchone()
        if not row or row['closed'] or row['starts_at']<=now() or row['revision']!=revision:raise ValueError('时段已变化或不可预约，请刷新。')
        if credits.calendar_enabled(con) and not row['published']:raise ValueError('该时段尚未发布。')
        cid=row['coach_id'];kind=row['kind']
        if not con.execute('SELECT 1 FROM student_coaches WHERE student_id=? AND coach_id=?',(sid,cid)).fetchone():raise auth.PermissionDenied('请先联系 Jamie 对接教练。')
        if not _available_coach(con,cid):raise ValueError('教练暂不可预约。')
        credits.require_balance(con,sid,cid,kind)
        svc._conflict(con,cid,row['starts_at'],row['ends_at'])
        _overlap_student(con,sid,row['starts_at'],row['ends_at'])
        lid=con.execute('INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location,booking_slot_id) VALUES(?,?,?,?,?,?,?)',
          (sid,cid,kind,row['starts_at'],row['ends_at'],row['location'],slot_id)).lastrowid
        con.execute('INSERT INTO student_booking_requests VALUES(?,?,?,?)',(sid,request_key,slot_id,lid))
        auth.audit(con,None,'student_book_lesson','lessons',lid,{'student_id':sid,'slot_id':slot_id})
        return lid


def my_lessons(token):
    with store.connect() as con:
        student=student_auth.require(con,token)
        # No contact details, other students, private profiles or staff audit fields.
        rows=[dict(r) for r in con.execute('''SELECT l.id,l.kind,l.starts_at,l.ends_at,l.location,l.status,l.revision,l.charged,l.checkin_done,c.name coach_name
          FROM lessons l JOIN coaches c ON c.id=l.coach_id WHERE l.student_id=? AND l.archived=0 ORDER BY l.starts_at DESC,l.id DESC''',(student['student_id'],))]
        if credits.calendar_enabled(con):
            flags={r['id']:r['attendance_confirmed'] for r in con.execute('SELECT id,attendance_confirmed FROM lessons WHERE student_id=?',(student['student_id'],))}
            for row in rows:row['can_cancel']=bool(row['status']=='待上课' and not row['charged'] and not row['checkin_done'] and not flags[row['id']] and can_cancel(row))
        return rows


def publish_week(token,week):
    from datetime import date,timedelta
    if isinstance(week,str):week=date.fromisoformat(week)
    finish=(week+timedelta(days=7)).isoformat()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token,credits.ROLES)
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        slots=con.execute('SELECT * FROM booking_slots WHERE closed=0 AND published=0 AND starts_at>=? AND starts_at<? AND starts_at>?',(week.isoformat(),finish,now())).fetchall()
        for row in slots:
            svc._conflict(con,row['coach_id'],row['starts_at'],row['ends_at'])
            if not _available_coach(con,row['coach_id']):raise ValueError('包含已停用教练，未发布任何时段。')
        for row in slots:
            con.execute('UPDATE booking_slots SET published=1,revision=revision+1 WHERE id=?',(row['id'],))
            auth.audit(con,user['id'],'publish_booking_slot','booking_slots',row['id'])
        return len(slots)


def calendar_slots(token,week):
    from datetime import date,timedelta
    if isinstance(week,str):week=date.fromisoformat(week)
    with store.connect() as con:
        student=student_auth.require(con,token);sid=student['student_id']
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        rows=con.execute('''SELECT b.*,c.name coach_name,
          EXISTS(SELECT 1 FROM lessons l WHERE l.coach_id=b.coach_id AND l.archived=0 AND l.status NOT IN ('已取消','已改期') AND l.starts_at<b.ends_at AND l.ends_at>b.starts_at) booked,
          EXISTS(SELECT 1 FROM lessons l WHERE l.student_id=? AND l.booking_slot_id=b.id AND l.archived=0 AND l.status NOT IN ('已取消','已改期')) mine,
          EXISTS(SELECT 1 FROM group_reminders g WHERE g.coach_id=b.coach_id AND g.archived=0 AND g.starts_at<b.ends_at AND g.ends_at>b.starts_at) group_conflict
          FROM booking_slots b JOIN coaches c ON c.id=b.coach_id
          WHERE b.closed=0 AND b.published=1 AND b.starts_at>=? AND b.starts_at<?
          ORDER BY b.starts_at,b.coach_id,b.id''',(sid,week.isoformat(),(week+timedelta(days=7)).isoformat())).fetchall()
        result=[]
        for row in rows:
            if not _available_coach(con,row['coach_id']):continue
            linked=con.execute('SELECT 1 FROM student_coaches WHERE student_id=? AND coach_id=?',(sid,row['coach_id'])).fetchone()
            available=credits.available(con,sid,row['coach_id'],row['kind'])
            state='我的预约' if row['mine'] else '已约' if row['booked'] else '不可预约' if row['group_conflict'] or row['starts_at']<=now() or not linked or available<1 else '可预约'
            result.append(dict(row,state=state,available=available))
        return result


def can_cancel(row,at=None):
    from datetime import timezone,timedelta
    start=datetime.fromisoformat(row['starts_at']).replace(tzinfo=rules.LONDON)
    at=at or rules.now_london()
    return start.astimezone(timezone.utc)-at.astimezone(timezone.utc)>=timedelta(hours=24)


def cancel_booking(token,lid,revision):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');student=student_auth.require(con,token)
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        row=con.execute('SELECT * FROM lessons WHERE id=? AND student_id=? AND archived=0',(lid,student['student_id'])).fetchone()
        if not row:raise auth.PermissionDenied('预约不存在或不属于你。')
        if row['revision']!=revision:raise ValueError('预约已变化，请刷新后重试。')
        if row['status'] in ('已取消','已改期','已完成') or row['charged'] or row['checkin_done'] or row['attendance_confirmed']:
            raise ValueError('这节课不能自行取消，请联系教练。')
        if not can_cancel(row):raise ValueError('距离开课不足24小时，请微信联系教练。')
        con.execute("UPDATE lessons SET status='已取消',reason='学员网页取消',cancellation_24h=1,absence=0,revision=revision+1 WHERE id=?",(lid,))
        from ops_reviews import invalidate
        invalidate(con,lid,'学员提前取消')
        auth.audit(con,None,'student_cancel_booking','lessons',lid,{'student_id':student['student_id']})
