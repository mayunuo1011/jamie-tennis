"""UI-facing services; checks and financial undo always execute on the server."""
from datetime import datetime,timedelta
import ops_auth as auth
import ops_store as store
import ops_credits as credits
import ops_reviews as reviews
import ops_service as svc
import user_service as rules


def undo_completion(token,lid,submission_id):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token,credits.ROLES)
        if not credits.cycles_enabled(con):raise ValueError('请先升级界面版本。')
        row=con.execute('SELECT * FROM lessons WHERE id=? AND archived=0',(lid,)).fetchone()
        current=con.execute('SELECT * FROM feedback_submissions WHERE lesson_id=? AND is_current=1',(lid,)).fetchone()
        if not row or not current or current['id']!=submission_id or current['state']!='approved' or row['status']!='已完成':
            raise ValueError('课程已经变化或已撤销，请刷新后查看。')
        refunded=False
        if credits.active_charge(con,lid):
            credits._return_in_transaction(con,user,lid,'撤销审核完成');refunded=True
        elif row['charged'] and not con.execute("SELECT 1 FROM credit_ledger WHERE lesson_id=? AND action='return'",(lid,)).fetchone():
            raise ValueError('只有历史扣费标记，没有可核对的扣课流水，请交由系统管理员处理。')
        con.execute("UPDATE lessons SET status='待上课',charged=0,charge_due=0,package_id=NULL,revision=revision+1 WHERE id=?",(lid,))
        reviews.invalidate(con,lid)
        pending_id=reviews.submit_current(con,lid,user['id'])
        auth.audit(con,user['id'],'undo_completion','lessons',lid,{'submission_id':submission_id,'new_submission_id':pending_id,'refunded':refunded})
        return {'pending_id':pending_id,'refunded':refunded}


def review_courses(token,start):
    with store.connect() as con:auth.require(con,token,credits.ROLES)
    records=svc.list_lessons(token,start)
    return sorted([r for r in records if r['kind'] in ('私教','陪打') and rules.attendance(r)=='上了'],key=lambda r:(r['starts_at'],r['id']))


def current_submission(token,lid):
    with store.connect() as con:
        user=auth.require(con,token,credits.ROLES)
        row=con.execute('SELECT id FROM feedback_submissions WHERE lesson_id=? AND is_current=1',(lid,)).fetchone()
    return reviews.open_submission(token,row['id']) if row else None


def student_summary(token,sid):
    with store.connect() as con:
        user=auth.require(con,token,credits.ROLES)
        if not con.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone():raise ValueError('学员不存在。')
        packages=con.execute('''SELECT p.*,c.name coach_name FROM packages p LEFT JOIN coaches c ON c.id=p.coach_id
          WHERE p.student_id=? AND p.coach_id IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM credit_ledger v WHERE v.package_id=p.id AND v.action='void_purchase')''',(sid,)).fetchall()
        balances={}
        for p in packages:
            key=(p['coach_name'],p['kind']);balances[key]=balances.get(key,0)+p['remaining']
        kinds=sorted({p['kind'] for p in packages})
        classes=[r[0] for r in con.execute('''SELECT DISTINCT g.name FROM group_reminders g JOIN group_members m ON m.group_id=g.id
          WHERE m.student_id=? AND g.archived=0 ORDER BY g.name''',(sid,))]
        assigned=[r[0] for r in con.execute('''SELECT c.name FROM coaches c WHERE c.id IN (
          SELECT coach_id FROM student_coaches WHERE student_id=?
          UNION SELECT coach_id FROM packages WHERE student_id=?
          UNION SELECT coach_id FROM lessons WHERE student_id=? AND archived=0) ORDER BY c.id''',(sid,sid,sid))]
        return {'coaches':' / '.join(assigned) or '待对接',
                'purchases':'；'.join(f'{c}·{k}' for c,k in balances) or '未录入',
                'balance':'；'.join(f'{c}·{k} {n}节' for (c,k),n in balances.items()) or '未录入',
                'classes':' / '.join(kinds+classes) or '待对接'}


def groups(token,start=None,coach_id=None):
    with store.connect() as con:
        user=auth.require(con,token)
        if user['role']=='coach':coach_id=user['coach_id']
        rows=con.execute('''SELECT g.*,c.name coach_name FROM group_reminders g JOIN coaches c ON c.id=g.coach_id
          WHERE g.archived=0 AND (? IS NULL OR g.coach_id=?) ORDER BY g.starts_at,g.id''',(coach_id,coach_id)).fetchall()
        result=[dict(r) for r in rows]
        if start:result=[r for r in result if start.isoformat()<=r['starts_at'][:10]<(start+timedelta(days=7)).isoformat()]
        return result


def save_group(token,name,start,end,location,members=(),gid=None,expected_revision=None):
    rules._validate_times(start,end)
    if not name.strip() or not location.strip():raise ValueError('请填写团课名称和地点。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token,credits.ROLES)
        if gid is None:
            if not user['coach_id']:raise ValueError('当前账号没有教练身份，不能新增自己的团课。')
            cid=user['coach_id']
        else:
            old=con.execute('SELECT * FROM group_reminders WHERE id=? AND archived=0',(gid,)).fetchone()
            if not old:raise ValueError('团课不存在。')
            svc.require_course_writer(user,old['coach_id']);cid=old['coach_id']
            if old['revision']!=expected_revision:raise ValueError('记录已变化，请重新打开。')
        for sid in set(members):
            if not con.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone():raise ValueError('团课成员不存在。')
        if credits.booking_enabled(con):
            if con.execute("SELECT 1 FROM lessons WHERE coach_id=? AND archived=0 AND status NOT IN ('已取消','已改期') AND starts_at<? AND ends_at>?",(cid,end,start)).fetchone():
                raise ValueError('与该教练已有课程时间重叠。')
            if con.execute('SELECT 1 FROM group_reminders WHERE coach_id=? AND archived=0 AND id!=? AND starts_at<? AND ends_at>?',(cid,gid or -1,end,start)).fetchone():
                raise ValueError('与已有团课时间重叠。')
        if gid is None:
            gid=con.execute('INSERT INTO group_reminders(coach_id,name,starts_at,ends_at,location) VALUES(?,?,?,?,?)',(cid,name.strip(),start,end,location.strip())).lastrowid
        else:
            con.execute('UPDATE group_reminders SET name=?,starts_at=?,ends_at=?,location=?,revision=revision+1 WHERE id=?',(name.strip(),start,end,location.strip(),gid))
            con.execute('DELETE FROM group_members WHERE group_id=?',(gid,))
        con.executemany('INSERT INTO group_members VALUES(?,?)',[(gid,sid) for sid in set(members)])
        auth.audit(con,user['id'],'save_group_reminder','group_reminders',gid)
        return gid


def group_detail(token,gid):
    rows=groups(token)
    row=next((r for r in rows if r['id']==gid),None)
    if not row:raise auth.PermissionDenied('团课不在可见范围内。')
    with store.connect() as con:
        auth.require(con,token)
        row['members']=[r[0] for r in con.execute('SELECT student_id FROM group_members WHERE group_id=?',(gid,))]
    return row


def archive_group(token,gid,revision):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=auth.require(con,token,credits.ROLES)
        row=con.execute('SELECT * FROM group_reminders WHERE id=? AND archived=0',(gid,)).fetchone()
        if not row or row['revision']!=revision:raise ValueError('团课不存在或已变化。')
        svc.require_course_writer(user,row['coach_id'])
        con.execute('UPDATE group_reminders SET archived=1,revision=revision+1 WHERE id=?',(gid,))
        auth.audit(con,user['id'],'archive_group_reminder','group_reminders',gid)
