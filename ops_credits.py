"""Server-authorized credits, scoped by pupil + teacher + kind. No payments."""
from datetime import date
import hashlib
import json
import ops_auth as auth
import ops_store as store
import user_service as rules

ROLES = ('operator', 'system_admin')


def enabled(con):
    return con.execute('SELECT 1 FROM ops_migrations WHERE version=4').fetchone() is not None


def cycles_enabled(con):
    return con.execute('SELECT 1 FROM ops_migrations WHERE version=5').fetchone() is not None


def booking_enabled(con):
    return con.execute('SELECT 1 FROM ops_migrations WHERE version=6').fetchone() is not None


def calendar_enabled(con):
    return con.execute('SELECT 1 FROM ops_migrations WHERE version=7').fetchone() is not None


def occupied(con,sid,cid,kind):
    if not booking_enabled(con):return 0
    return con.execute("""SELECT count(*) FROM lessons WHERE student_id=? AND coach_id=? AND kind=?
      AND archived=0 AND charged=0 AND (status NOT IN ('已取消','已改期') OR (status='已改期' AND reschedule_pending=1))""",
      (sid,cid,kind)).fetchone()[0]


def available(con,sid,cid,kind):
    return balance(con,sid,cid,kind)-occupied(con,sid,cid,kind)


def active_charge(con,lid):
    extra=' AND active=1' if cycles_enabled(con) else ''
    row=con.execute("SELECT * FROM credit_ledger WHERE lesson_id=? AND action IN ('lesson','absence')"+extra,(lid,)).fetchone()
    if not cycles_enabled(con) and row and con.execute("SELECT 1 FROM credit_ledger WHERE lesson_id=? AND action='return'",(lid,)).fetchone():return None
    return row


def _require(con, token):
    user = auth.require(con, token, ROLES)
    if not enabled(con):
        raise ValueError('请先关闭网站，运行“升级课时管理.bat”。')
    return user


def balance(con, sid, cid, kind):
    return con.execute('SELECT COALESCE(SUM(remaining),0) FROM packages WHERE student_id=? AND coach_id=? AND kind=?',
                       (sid, cid, kind)).fetchone()[0]


def require_balance(con, sid, cid, kind):
    if enabled(con) and available(con, sid, cid, kind) < 1:
        raise ValueError('未购买该教练的对应课程，或课时已用完。请 Jamie 先录入课包，暂不能排课。')


def _entry(con, user, package, action, delta, lesson_id=None, reason=''):
    return con.execute('''INSERT INTO credit_ledger(student_id,coach_id,kind,package_id,lesson_id,action,delta,actor_id,reason,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?)''', (package['student_id'], package['coach_id'], package['kind'], package['id'], lesson_id,
      action, delta, user['id'] if user else None, reason, rules.now_london().isoformat(timespec='seconds'))).lastrowid


def add_package(token, sid, cid, kind, purchased_at, quantity, request_key):
    if type(quantity) is not int or not 1 <= quantity <= 10000:
        raise ValueError('购买课时必须是1至10000的整数。')
    if kind not in ('私教', '陪打'):
        raise ValueError('课程类型不正确。')
    day = date.fromisoformat(str(purchased_at)).isoformat()
    if not isinstance(request_key, str) or not 10 <= len(request_key) <= 100:
        raise ValueError('缺少提交标识，请重新载入表单。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user = _require(con, token)
        return _add_package_tx(con,user,sid,cid,kind,day,quantity,request_key)


def _add_package_tx(con,user,sid,cid,kind,day,quantity,request_key,fingerprint=None):
    fingerprint = fingerprint or hashlib.sha256(json.dumps([sid,cid,kind,day,quantity],ensure_ascii=False).encode()).hexdigest()
    if not con.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone():
        raise ValueError('学员不存在。')
    if not con.execute('SELECT 1 FROM coaches WHERE id=?',(cid,)).fetchone():
        raise ValueError('教练不存在。')
    existing = con.execute('SELECT * FROM credit_requests WHERE account_id=? AND request_key=?',(user['id'],request_key)).fetchone()
    if existing:
        if existing['fingerprint'] != fingerprint:
            raise ValueError('提交标识已用于其他内容，请重新打开表单。')
        return existing['package_id']
    pid = con.execute('INSERT INTO packages(student_id,coach_id,kind,purchased_at,purchased,remaining) VALUES(?,?,?,?,?,?)',
                      (sid,cid,kind,day,quantity,quantity)).lastrowid
    package = con.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
    _entry(con,user,package,'purchase',quantity)
    con.execute('INSERT OR IGNORE INTO student_coaches VALUES(?,?)',(sid,cid))
    con.execute('INSERT INTO credit_requests VALUES(?,?,?,?)',(user['id'],request_key,fingerprint,pid))
    auth.audit(con,user['id'],'purchase_credits','packages',pid,{'coach_id':cid,'kind':kind,'quantity':quantity})
    return pid


def register_student_package(token,name,cid,kind,purchased_at,quantity,request_key,contact='',source=''):
    """One atomic registration: no half-created pupil if credit entry fails."""
    if not isinstance(name,str) or not name.strip():raise ValueError('请填写学员称呼。')
    if type(quantity) is not int or not 1<=quantity<=10000:raise ValueError('购买课时必须是1至10000的整数。')
    if kind not in ('私教','陪打'):raise ValueError('课程类型不正确。')
    day=date.fromisoformat(str(purchased_at)).isoformat()
    if not isinstance(request_key,str) or not 10<=len(request_key)<=100:raise ValueError('缺少提交标识，请刷新。')
    values=[name.strip(),contact.strip(),source.strip(),cid,kind,day,quantity]
    fingerprint=hashlib.sha256(json.dumps(['register']+values,ensure_ascii=False).encode()).hexdigest()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user=_require(con,token)
        cached=con.execute('SELECT * FROM credit_requests WHERE account_id=? AND request_key=?',(user['id'],request_key)).fetchone()
        if cached:
            if cached['fingerprint']!=fingerprint:raise ValueError('提交标识已用于其他内容，请刷新。')
            return con.execute('SELECT student_id FROM packages WHERE id=?',(cached['package_id'],)).fetchone()[0]
        if not con.execute('SELECT 1 FROM coaches WHERE id=?',(cid,)).fetchone():raise ValueError('教练不存在。')
        sid=con.execute('INSERT INTO students(name,contact,source,joined_at) VALUES(?,?,?,?)',
          (values[0],values[1],values[2],rules.today_london().isoformat())).lastrowid
        auth.audit(con,user['id'],'create_student','students',sid)
        _add_package_tx(con,user,sid,cid,kind,day,quantity,request_key,fingerprint)
        return sid


def packages(token, sid=None):
    with store.connect() as con:
        _require(con,token)
        rows = con.execute('''SELECT p.*,s.name student_name,c.name coach_name,
          EXISTS(SELECT 1 FROM credit_ledger x WHERE x.package_id=p.id AND x.action='void_purchase') voided,
          EXISTS(SELECT 1 FROM credit_ledger x WHERE x.package_id=p.id AND x.action IN ('lesson','absence','return')) used
          FROM packages p JOIN students s ON s.id=p.student_id LEFT JOIN coaches c ON c.id=p.coach_id
          WHERE (? IS NULL OR p.student_id=?) ORDER BY p.purchased_at DESC,p.id DESC''',(sid,sid)).fetchall()
        return [dict(row) for row in rows]


def balances(token, sid=None):
    with store.connect() as con:
        _require(con,token)
        return [dict(r) for r in con.execute('''SELECT p.student_id,s.name student_name,p.coach_id,c.name coach_name,p.kind,
          SUM(p.remaining) remaining FROM packages p JOIN students s ON s.id=p.student_id
          JOIN coaches c ON c.id=p.coach_id WHERE (? IS NULL OR p.student_id=?)
          GROUP BY p.student_id,s.name,p.coach_id,c.name,p.kind ORDER BY s.name,c.name,p.kind''',(sid,sid))]


def history(token, sid=None):
    with store.connect() as con:
        _require(con,token)
        return [dict(r) for r in con.execute('''SELECT x.id,x.student_id,s.name student_name,c.name coach_name,x.kind,
          x.package_id,x.lesson_id,x.action,x.delta,x.reason,x.created_at,a.display_name actor_name
          FROM credit_ledger x JOIN students s ON s.id=x.student_id JOIN coaches c ON c.id=x.coach_id
          LEFT JOIN accounts a ON a.id=x.actor_id WHERE (? IS NULL OR x.student_id=?) ORDER BY x.id DESC''',(sid,sid))]


def void_package(token, pid, reason):
    if not reason.strip():
        raise ValueError('请填写作废原因。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user = _require(con,token)
        p = con.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
        if not p or p['coach_id'] is None:
            raise ValueError('课包不存在或尚未明确教练归属。')
        if con.execute("SELECT 1 FROM credit_ledger WHERE package_id=? AND action IN ('void_purchase','lesson','absence','return')",(pid,)).fetchone() or p['remaining']!=p['purchased']:
            raise ValueError('仅能作废尚未使用的完整课包，已使用或已作废的不能直接删除。')
        if booking_enabled(con) and available(con,p['student_id'],p['coach_id'],p['kind'])<p['remaining']:
            raise ValueError('撤销后课时不足以覆盖已有预约或欠课，请先联系教练处理预约。')
        con.execute('UPDATE packages SET remaining=0 WHERE id=?',(pid,))
        _entry(con,user,p,'void_purchase',-p['remaining'],reason=reason.strip())
        auth.audit(con,user['id'],'void_unused_package','packages',pid,{'reason':reason.strip()})


def charge(con, user, lid, action='lesson'):
    """Caller owns the approval transaction; raising rolls back approval too."""
    automatic_absence = action=='absence' and booking_enabled(con)
    if user['role'] not in ROLES and not (automatic_absence and user['role']=='coach'):
        raise auth.PermissionDenied('只有 Jamie 或系统管理员可以扣课。')
    row = con.execute('SELECT * FROM lessons WHERE id=? AND archived=0',(lid,)).fetchone()
    if not row or row['coach_id'] is None:
        raise ValueError('课程不存在或尚未确定教练。')
    if automatic_absence:
        from ops_service import require_course_writer
        if user['role']=='coach':require_course_writer(user,row['coach_id'])
        if row['status']!='已取消' or not row['absence'] or not row['charge_due']:
            raise ValueError('只有已登记的应扣缺课才能自动扣课。')
    prior = active_charge(con,lid) if cycles_enabled(con) else con.execute("SELECT id FROM credit_ledger WHERE lesson_id=? AND action IN ('lesson','absence')",(lid,)).fetchone()
    if prior:
        return 'already_charged'
    if row['charged']:
        raise ValueError('课程已有历史扣课标记，请交由系统管理员核对，不能再次扣课。')
    p = con.execute('''SELECT * FROM packages WHERE student_id=? AND coach_id=? AND kind=? AND remaining>=1
      ORDER BY purchased_at,id LIMIT 1''',(row['student_id'],row['coach_id'],row['kind'])).fetchone()
    if not automatic_absence and booking_enabled(con) and balance(con,row['student_id'],row['coach_id'],row['kind'])<1:
        raise ValueError('对应课时余额不足，未扣课、未完成本次审核；请先补录课包。')
    if not p and automatic_absence:
        p=con.execute("""SELECT * FROM packages WHERE student_id=? AND coach_id=? AND kind=?
          AND NOT EXISTS(SELECT 1 FROM credit_ledger v WHERE v.package_id=packages.id AND v.action='void_purchase')
          ORDER BY purchased_at,id LIMIT 1""",(row['student_id'],row['coach_id'],row['kind'])).fetchone()
        if not p:
            pid=con.execute('INSERT INTO packages(student_id,coach_id,kind,purchased_at,purchased,remaining,is_debt) VALUES(?,?,?,?,0,0,1)',
                (row['student_id'],row['coach_id'],row['kind'],rules.today_london().isoformat())).lastrowid
            p=con.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
    if not p:
        raise ValueError('对应课时余额不足，未扣课、未完成本次审核；请先补录课包。')
    con.execute('UPDATE packages SET remaining=remaining-1 WHERE id=?',(p['id'],))
    _entry(con,user,p,action,-1,lid)
    con.execute('UPDATE lessons SET charged=1,charge_due=0,package_id=? WHERE id=?',(p['id'],lid))
    auth.audit(con,user['id'],'deduct_credit','lessons',lid,{'package_id':p['id'],'kind':row['kind'],'charge_reason':action})
    return 'charged'


def pending_absences(token):
    with store.connect() as con:
        _require(con,token)
        return [dict(r) for r in con.execute('''SELECT l.id,l.student_id,s.name student_name,c.name coach_name,l.kind,l.starts_at,l.reason
          FROM lessons l JOIN students s ON s.id=l.student_id JOIN coaches c ON c.id=l.coach_id
          WHERE l.archived=0 AND l.status='已取消' AND l.absence=1 AND l.charge_due=1 AND l.charged=0 ORDER BY l.starts_at''')]


def settle_absence(token,lid):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user = _require(con,token)
        row = con.execute("SELECT * FROM lessons WHERE id=? AND archived=0 AND status='已取消' AND absence=1",(lid,)).fetchone()
        if not row or not row['charge_due'] or row['charged']:
            raise ValueError('这节课没有待处理缺课扣费，不能重复处理。')
        if booking_enabled(con):
            # Historical pending rows are not silently back-charged. An explicit
            # operator action is still permitted, using the newly confirmed rule.
            return charge(con,user,lid,'absence')
        if balance(con,row['student_id'],row['coach_id'],row['kind'])<1:
            return 'unpaid'
        return charge(con,user,lid,'absence')


def return_credit(token,lid,reason):
    if not reason.strip():raise ValueError('请填写退还课时的原因。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');user = _require(con,token)
        _return_in_transaction(con,user,lid,reason)


def _return_in_transaction(con,user,lid,reason):
    charge_row=active_charge(con,lid)
    if not charge_row:raise ValueError('课程没有可退还的扣课记录，或已经退还。')
    p=con.execute('SELECT * FROM packages WHERE id=?',(charge_row['package_id'],)).fetchone()
    con.execute('UPDATE packages SET remaining=remaining+1 WHERE id=?',(p['id'],))
    entry=_entry(con,user,p,'return',1,lid,reason.strip())
    if cycles_enabled(con):
        con.execute('UPDATE credit_ledger SET active=0 WHERE id=?',(charge_row['id'],))
        con.execute('UPDATE credit_ledger SET reverses_id=? WHERE id=?',(charge_row['id'],entry))
    auth.audit(con,user['id'],'return_credit','lessons',lid,{'package_id':p['id'],'reason':reason.strip(),'charge_entry':charge_row['id']})
    return entry


def lesson_credit_state(con,lid):
    if not enabled(con):return '未扣课（尚未接入）'
    if cycles_enabled(con) and active_charge(con,lid):return '已扣1节'
    actions = {r[0] for r in con.execute('SELECT action FROM credit_ledger WHERE lesson_id=?',(lid,))}
    if 'return' in actions:return '已退还1节'
    if actions & {'lesson','absence'}:return '已扣1节'
    row = con.execute('SELECT charged,charge_due FROM lessons WHERE id=?',(lid,)).fetchone()
    if row['charged']:return '历史已扣课'
    if row['charge_due']:return '待处理缺课扣费（未扣）'
    return '未扣课'
