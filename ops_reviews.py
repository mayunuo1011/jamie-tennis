"""Immutable feedback snapshots and role-checked review; no credit mutations."""
from datetime import datetime
import ops_auth as auth
import ops_store as store
import user_service as rules

ALLOW_REOPEN_AFTER_APPROVAL=False  # Confirmed: Jamie must return approved feedback before edits.
STATES={'pending':'待审核','approved':'已审核完成','returned':'已退回修改','withdrawn':'已撤回'}


def _now():return rules.now_london().isoformat(timespec='seconds')


def approved(con,lid):
    return con.execute("SELECT 1 FROM feedback_submissions WHERE lesson_id=? AND is_current=1 AND state='approved'",(lid,)).fetchone() is not None


def before_feedback_change(con,lid):
    current=con.execute('SELECT * FROM feedback_submissions WHERE lesson_id=? AND is_current=1',(lid,)).fetchone()
    from ops_credits import enabled
    if current and current['state']=='approved' and not ALLOW_REOPEN_AFTER_APPROVAL and not enabled(con):
        raise ValueError('反馈已审核完成，请先由 Jamie 退回后再修改或删除。')


def invalidate(con,lid,reason='反馈更新'):
    current=con.execute('SELECT id,state FROM feedback_submissions WHERE lesson_id=? AND is_current=1',(lid,)).fetchone()
    if current:
        state='withdrawn' if current['state']=='pending' else current['state']
        con.execute('UPDATE feedback_submissions SET is_current=0,state=? WHERE id=?',(state,current['id']))


def submit_current(con,lid,actor_id,source='submission'):
    row=con.execute('SELECT * FROM lessons WHERE id=?',(lid,)).fetchone()
    if not row or row['archived'] or row['coach_id'] is None or row['status'] in ('已取消','已改期'):
        raise ValueError('课程不符合提交条件。')
    note,goal=rules.validate_checkin(row['note'],row['next_goal'])
    old=con.execute('SELECT id FROM feedback_submissions WHERE lesson_id=? AND feedback_revision=?',(lid,row['revision'])).fetchone()
    if old:return old['id']
    from ops_credits import enabled
    previous=con.execute("SELECT * FROM feedback_submissions WHERE lesson_id=? AND is_current=1 AND state='approved'",(lid,)).fetchone() if enabled(con) else None
    invalidate(con,lid)
    sid=con.execute('''INSERT INTO feedback_submissions(lesson_id,coach_id,feedback_revision,note,next_goal,submitted_by,submitted_at,state,source)
      VALUES(?,?,?,?,?,?,?,'pending',?)''',(lid,row['coach_id'],row['revision'],note,goal,actor_id,(row['checked_in_at'] or _now()) if source=='migration' else _now(),source)).lastrowid
    if previous:
        con.execute("UPDATE feedback_submissions SET state='approved',reviewed_by=?,reviewed_at=?,review_comment=? WHERE id=?",
          (previous['reviewed_by'],previous['reviewed_at'],previous['review_comment'],sid))
    return sid


def _submission(con,user,sid):
    row=con.execute('''SELECT f.*,l.student_id,l.starts_at,l.ends_at,l.location,l.kind,
      l.status lesson_status,l.revision lesson_revision,l.archived,l.checkin_done,
      s.name student_name,c.name coach_name,a.display_name reviewer_name
      FROM feedback_submissions f JOIN lessons l ON l.id=f.lesson_id
      JOIN students s ON s.id=l.student_id JOIN coaches c ON c.id=f.coach_id
      LEFT JOIN accounts a ON a.id=f.reviewed_by WHERE f.id=?''',(sid,)).fetchone()
    if not row or (user['role']=='coach' and row['coach_id']!=user['coach_id']):
        raise auth.PermissionDenied('该反馈不在你的可见范围内。')
    return dict(row)


def list_submissions(token,state=None,history=False):
    with store.connect() as con:
        user=auth.require(con,token)
        clauses=[];values=[]
        if user['role']=='coach':clauses.append('coach_id=?');values.append(user['coach_id'])
        if not history:clauses.append('is_current=1')
        if state:
            if state not in STATES:raise ValueError('审核状态不正确。')
            clauses.append('state=?');values.append(state)
        ids=[r[0] for r in con.execute('SELECT id FROM feedback_submissions'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY submitted_at DESC,id DESC',values)]
        return [_submission(con,user,sid) for sid in ids]


def open_submission(token,sid):
    with store.connect() as con:
        user=auth.require(con,token)
        row=_submission(con,user,sid)
        con.execute('INSERT INTO feedback_reads VALUES(?,?,?) ON CONFLICT(submission_id,account_id) DO NOTHING',(sid,user['id'],_now()))
        return row


def inbox_stats(token):
    with store.connect() as con:
        user=auth.require(con,token)
        where='f.is_current=1';values=[user['id']]
        if user['role']=='coach':where+=' AND f.coach_id=?';values.append(user['coach_id'])
        row=con.execute('''SELECT COALESCE(SUM(CASE WHEN f.state='pending' THEN 1 ELSE 0 END),0) pending,
          COALESCE(SUM(CASE WHEN f.state='returned' THEN 1 ELSE 0 END),0) returned,
          COALESCE(SUM(CASE WHEN f.state='approved' THEN 1 ELSE 0 END),0) approved,
          COALESCE(SUM(CASE WHEN f.state='pending' AND r.submission_id IS NULL THEN 1 ELSE 0 END),0) unread,
          COALESCE(MAX(f.id),0) latest_id
          FROM feedback_submissions f LEFT JOIN feedback_reads r ON r.submission_id=f.id AND r.account_id=? WHERE '''+where,values).fetchone()
        return dict(row)


def decide(token,sid,decision,comment=''):
    if decision not in ('approved','returned'):raise ValueError('审核操作不正确。')
    if decision=='returned' and not comment.strip():raise ValueError('请填写退回原因。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token,('operator','system_admin'))
        row=_submission(con,user,sid)
        if not row['is_current'] or row['archived'] or row['lesson_status'] in ('已取消','已改期'):
            raise ValueError('反馈已更新、撤回或课程已取消改期，请刷新待审核列表。')
        if decision=='approved':
            if row['state']!='pending':raise ValueError('该反馈已处理，不会重复确认。')
            if row['lesson_revision']!=row['feedback_revision'] or not row['checkin_done']:
                raise ValueError('课程记录已变化，请教练重新提交最新反馈。')
            if rules.attendance({'status':row['lesson_status'],'ends_at':row['ends_at']})!='上了':
                raise ValueError('课程尚未结束，暂不能确认完成。')
            status='已完成'
        else:
            from ops_credits import enabled
            if enabled(con) and row['state']=='approved':raise ValueError('已完成的课程保留完成状态，教练可直接修改反馈，不能再次退回或扣课。')
            if row['state'] not in ('pending','approved'):raise ValueError('该反馈已退回或撤回。')
            status='待上课'
        from ops_credits import enabled,charge
        credit_action=charge(con,user,row['lesson_id']) if decision=='approved' and enabled(con) else 'none'
        con.execute('UPDATE feedback_submissions SET state=?,reviewed_by=?,reviewed_at=?,review_comment=? WHERE id=?',
            (decision,user['id'],_now(),comment.strip(),sid))
        con.execute('UPDATE lessons SET status=?,revision=revision+1 WHERE id=?',(status,row['lesson_id']))
        con.execute('INSERT INTO feedback_reads VALUES(?,?,?) ON CONFLICT(submission_id,account_id) DO NOTHING',(sid,user['id'],_now()))
        auth.audit(con,user['id'],'review_feedback_'+decision,'feedback_submissions',sid,
            {'lesson_id':row['lesson_id'],'feedback_revision':row['feedback_revision'],'comment':comment.strip(),'credit_action':credit_action})
        return {'state':decision,'credit_action':credit_action}


def weekly_overview(token,start):
    with store.connect() as con:auth.require(con,token,('operator','system_admin'))
    from ops_service import list_lessons
    records=list_lessons(token,start)
    with store.connect() as con:
        auth.require(con,token,('operator','system_admin'))
        flags={r['id']:r['charged'] for r in con.execute('SELECT id,charged FROM lessons WHERE archived=0')}
        from ops_credits import lesson_credit_state
        credit_states={r['id']:lesson_credit_state(con,r['id']) for r in records}
    return [{
        '学员':r['student_name'],'教练':r['coach_name'] or '待确认归属','类型':r['kind'],
        '日期':r['starts_at'][:10],'时段':r['starts_at'][11:16]+'–'+r['ends_at'][11:16],
        '地点':r['location'],'上课状态':rules.attendance(r),
        '打卡':'已打卡' if r['checkin_done'] else '未打卡',
        '反馈审核':STATES.get(r['review_state'],'未有效提交'),
        '确认完成':'已确认完成' if r['review_state']=='approved' else '未确认',
        '扣课':credit_states[r['id']] if credit_states[r['id']]!='未扣课（尚未接入）' else ('历史已扣课' if flags.get(r['id']) else '未扣课（尚未接入）'),
    } for r in records]
