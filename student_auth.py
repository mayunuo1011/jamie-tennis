"""Student credentials are separate from staff accounts and staff sessions."""
import re
import secrets
import time
import ops_auth as auth
import ops_store as store
import ops_credits as credits


def require(con,token,allow_initial=False):
    if not credits.booking_enabled(con):raise ValueError('请先升级预约版本。')
    now=int(time.time())
    row=con.execute('''SELECT a.student_id,a.active,a.must_change,s.name,x.expires_at,x.last_seen
      FROM student_sessions x JOIN student_accounts a ON a.student_id=x.student_id
      JOIN students s ON s.id=a.student_id WHERE x.token_hash=?''',(auth._token_hash(token),)).fetchone()
    if not row or not row['active'] or row['expires_at']<=now or row['last_seen']<=now-auth.IDLE_SECONDS:
        raise auth.AuthenticationError('请重新登录学员账号。')
    if row['must_change'] and not allow_initial and not credits.calendar_enabled(con):raise auth.PermissionDenied('请先修改初始密码。')
    con.execute('UPDATE student_sessions SET last_seen=? WHERE token_hash=?',(now,auth._token_hash(token)))
    return dict(row)


def current(token):
    with store.connect() as con:return require(con,token,True)


def provision(staff_token,sid,reset=False):
    with store.connect() as con:
        auth.require(con,staff_token,credits.ROLES)
        modern=credits.calendar_enabled(con)
    if modern:return provision_alias(staff_token,sid,reset)
    password=secrets.token_urlsafe(18)
    encoded=auth.password_hash(password)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');actor=auth.require(con,staff_token,credits.ROLES)
        if not credits.booking_enabled(con):raise ValueError('请先升级预约版本。')
        if not con.execute('SELECT 1 FROM students WHERE id=?',(sid,)).fetchone():raise ValueError('学员不存在。')
        exists=con.execute('SELECT 1 FROM student_accounts WHERE student_id=?',(sid,)).fetchone()
        if exists and not reset:raise ValueError('该学员已有账号；需要恢复登录时请确认重置密码。')
        con.execute('''INSERT INTO student_accounts(student_id,password_hash) VALUES(?,?)
          ON CONFLICT(student_id) DO UPDATE SET password_hash=excluded.password_hash,must_change=1''',(sid,encoded))
        con.execute('DELETE FROM student_sessions WHERE student_id=?',(sid,))
        auth.audit(con,actor['id'],'reset_student_password' if exists else 'create_student_login','students',sid)
    return {'username':f'S{sid:03d}','initial_password':password}


def set_active(staff_token,sid,active):
    if type(active) is not bool:raise ValueError('账号状态不正确。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');actor=auth.require(con,staff_token,credits.ROLES)
        if not con.execute('UPDATE student_accounts SET active=? WHERE student_id=?',(int(active),sid)).rowcount:
            raise ValueError('请先创建学员账号。')
        con.execute('DELETE FROM student_sessions WHERE student_id=?',(sid,))
        auth.audit(con,actor['id'],'set_student_active','students',sid,{'active':active})


def login(username,password=None):
    with store.connect() as con:
        modern=credits.calendar_enabled(con)
    if modern:return login_alias(username)
    username=str(username or '').strip().upper()[:64]
    match=re.fullmatch(r'S?([0-9]{1,12})',username)
    sid=int(match[1]) if match else -1
    key=f'S{sid:03d}' if sid>=0 else 'invalid-id'
    now=int(time.time());token=None
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if not credits.booking_enabled(con):raise ValueError('请先升级预约版本。')
        limit=con.execute('SELECT * FROM student_login_limits WHERE username=?',(key,)).fetchone()
        if limit and limit['blocked_until']>now:raise auth.AuthenticationError('登录暂时受限，请稍后再试。')
        row=con.execute('SELECT * FROM student_accounts WHERE student_id=?',(sid,)).fetchone()
        dummy='scrypt$131072$8$1$'+'00'*16+'$'+'00'*32
        matched=auth.verify_password(password,row['password_hash'] if row else dummy)
        if not row or not row['active'] or not matched:
            count=limit['failures']+1 if limit and now-limit['window_start']<auth.LOGIN_WINDOW else 1
            start=limit['window_start'] if limit and now-limit['window_start']<auth.LOGIN_WINDOW else now
            con.execute('INSERT INTO student_login_limits VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until',
              (key,count,start,now+auth.LOGIN_WINDOW if count>=auth.MAX_FAILURES else 0))
        else:
            token=secrets.token_urlsafe(32)
            con.execute('DELETE FROM student_login_limits WHERE username=?',(key,))
            con.execute('DELETE FROM student_sessions WHERE expires_at<=? OR last_seen<=?',(now,now-auth.IDLE_SECONDS))
            con.execute('INSERT INTO student_sessions VALUES(?,?,?,?,?)',(auth._token_hash(token),sid,now,now+auth.SESSION_SECONDS,now))
            auth.audit(con,None,'student_login','students',sid)
    if token is None:raise auth.AuthenticationError('学员 ID 或密码不正确。')
    return token


def change_password(token,old_password,new_password):
    with store.connect() as con:
        require(con,token,True)
        if credits.calendar_enabled(con):raise ValueError('学员端已取消密码环节。')
    encoded=auth.password_hash(new_password)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');student=require(con,token,True);sid=student['student_id']
        row=con.execute('SELECT password_hash FROM student_accounts WHERE student_id=?',(sid,)).fetchone()
        if not auth.verify_password(old_password,row[0]):raise ValueError('原密码不正确。')
        if auth.verify_password(new_password,row[0]):raise ValueError('新密码不能与原密码相同。')
        con.execute('UPDATE student_accounts SET password_hash=?,must_change=0 WHERE student_id=?',(encoded,sid))
        con.execute('DELETE FROM student_sessions WHERE student_id=?',(sid,))
        auth.audit(con,None,'student_change_password','students',sid)


def logout(token):
    with store.connect() as con:
        student=require(con,token,True)
        con.execute('DELETE FROM student_sessions WHERE token_hash=?',(auth._token_hash(token),))
        auth.audit(con,None,'student_logout','students',student['student_id'])


def nickname_prefix(name):
    from text_unidecode import unidecode
    prefix=re.sub('[^a-z0-9]','',unidecode(name).casefold())[:30]
    return prefix or 'student'


def provision_alias(staff_token,sid,rotate=False):
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE');actor=auth.require(con,staff_token,credits.ROLES)
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        student=con.execute('SELECT name FROM students WHERE id=?',(sid,)).fetchone()
        if not student:raise ValueError('学员不存在。')
        old=con.execute('SELECT login_name FROM student_accounts WHERE student_id=?',(sid,)).fetchone()
        if old and old['login_name'] and not rotate:return {'username':old['login_name']}
        prefix=nickname_prefix(student['name']);begin=secrets.randbelow(1000)
        for offset in range(1000):
            alias=f'{prefix}{(begin+offset)%1000:03d}'
            if not con.execute('SELECT 1 FROM student_accounts WHERE login_name=? COLLATE NOCASE',(alias,)).fetchone():break
        else:raise ValueError('该昵称的三位数字账号已用完，请调整昵称。')
        con.execute("""INSERT INTO student_accounts(student_id,password_hash,must_change,login_name) VALUES(?,'disabled-passwordless',0,?)
          ON CONFLICT(student_id) DO UPDATE SET password_hash='disabled-passwordless',must_change=0,login_name=excluded.login_name""",(sid,alias))
        con.execute('DELETE FROM student_sessions WHERE student_id=?',(sid,))
        auth.audit(con,actor['id'],'rotate_student_alias' if rotate else 'create_student_alias','students',sid)
        return {'username':alias}


def login_alias(username):
    key=str(username or '').strip().casefold()[:64]
    now=int(time.time());token=None
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if not credits.calendar_enabled(con):raise ValueError('请先升级日历版本。')
        limit=con.execute('SELECT * FROM student_login_limits WHERE username=?',(key,)).fetchone()
        if limit and limit['blocked_until']>now:raise auth.AuthenticationError('进入暂时受限，请稍后再试。')
        row=con.execute('SELECT * FROM student_accounts WHERE login_name=? COLLATE NOCASE',(key,)).fetchone() if re.fullmatch('[a-z0-9]{4,33}',key) else None
        if not row or not row['active']:
            count=limit['failures']+1 if limit and now-limit['window_start']<auth.LOGIN_WINDOW else 1
            start=limit['window_start'] if limit and now-limit['window_start']<auth.LOGIN_WINDOW else now
            con.execute('INSERT INTO student_login_limits VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until',
              (key,count,start,now+auth.LOGIN_WINDOW if count>=auth.MAX_FAILURES else 0))
        else:
            token=secrets.token_urlsafe(32)
            con.execute('DELETE FROM student_login_limits WHERE username=?',(key,))
            con.execute('DELETE FROM student_sessions WHERE expires_at<=? OR last_seen<=?',(now,now-auth.IDLE_SECONDS))
            con.execute('INSERT INTO student_sessions VALUES(?,?,?,?,?)',(auth._token_hash(token),row['student_id'],now,now+auth.SESSION_SECONDS,now))
            auth.audit(con,None,'student_alias_login','students',row['student_id'])
    if token is None:raise auth.AuthenticationError('账号不正确或已停用，请联系 Jamie。')
    return token
