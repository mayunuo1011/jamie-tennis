"""Server-side credentials, opaque sessions, role checks and audited account changes."""
import hashlib
import hmac
import json
import re
import secrets
import time

import ops_store as store

ROLES=('coach','operator','system_admin')
SESSION_SECONDS=8*3600
IDLE_SECONDS=30*60
LOGIN_WINDOW=15*60
MAX_FAILURES=5


class AuthenticationError(ValueError):pass
class PermissionDenied(ValueError):pass


def _username(value):
    value=value.strip().casefold()
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}',value):
        raise ValueError('账号需要 3–64 位英文字母、数字或 _ . -。')
    return value


def password_hash(password, salt=None):
    if not isinstance(password,str) or not 15<=len(password)<=128:
        raise ValueError('密码请使用 15–128 个字符，支持长口令。')
    salt=salt or secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode('utf-8'),salt=bytes.fromhex(salt),
        n=2**17,r=8,p=1,maxmem=256*1024*1024,dklen=32)
    return f'scrypt$131072$8$1${salt}${digest.hex()}'


def verify_password(password, stored):
    if not isinstance(password,str) or len(password)>128:return False
    try:
        algorithm,n,r,p,salt,digest=stored.split('$')
        if (algorithm,n,r,p)!=('scrypt','131072','8','1'):return False
        result=hashlib.scrypt(password.encode('utf-8'),salt=bytes.fromhex(salt),
            n=131072,r=8,p=1,maxmem=256*1024*1024,dklen=32)
        return hmac.compare_digest(result.hex(),digest)
    except (ValueError,TypeError):return False


def audit(con, actor_id, action, entity, entity_id=None, details=None):
    con.execute('INSERT INTO ops_audit(actor_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',
        (actor_id,action,entity,entity_id,json.dumps(details or {},ensure_ascii=False),int(time.time())))


def _insert_account(con, username, name, encoded, role):
    username=_username(username)
    if role not in ROLES:raise ValueError('角色不正确。')
    if not name.strip():raise ValueError('请填写显示名称。')
    if con.execute('SELECT 1 FROM accounts WHERE username=?',(username,)).fetchone():
        raise ValueError('账号已存在。')
    coach_id=None
    if role in ('coach','operator'):
        coach_id=con.execute('INSERT INTO coaches(name) VALUES(?)',(name.strip(),)).lastrowid
    return con.execute('INSERT INTO accounts(username,display_name,password_hash,role,coach_id,created_at) VALUES(?,?,?,?,?,?)',
        (username,name.strip(),encoded,role,coach_id,int(time.time()))).lastrowid


def bootstrap_admin(username, name, password):
    """Local provisioning only, never exposed by the Web UI."""
    encoded=password_hash(password)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():
            raise PermissionDenied('首个管理员已创建，请登录后管理账号。')
        uid=_insert_account(con,username,name,encoded,'system_admin')
        audit(con,uid,'bootstrap_admin','accounts',uid)
        return uid


def _token_hash(token):
    if not isinstance(token,str) or not 30<=len(token)<=200:
        raise AuthenticationError('请重新登录。')
    return hashlib.sha256(token.encode()).hexdigest()


def require(con, token, roles=None):
    digest=_token_hash(token)
    now=int(time.time())
    row=con.execute('''SELECT a.id,a.username,a.display_name,a.role,a.coach_id,a.active,
       s.expires_at,s.last_seen FROM auth_sessions s JOIN accounts a ON a.id=s.account_id
       WHERE s.token_hash=?''',(digest,)).fetchone()
    if not row or not row['active'] or row['expires_at']<=now or row['last_seen']<=now-IDLE_SECONDS:
        raise AuthenticationError('登录已失效，请重新登录。')
    if roles and row['role'] not in roles:
        raise PermissionDenied('当前账号没有操作权限。')
    con.execute('UPDATE auth_sessions SET last_seen=? WHERE token_hash=?',(now,digest))
    return dict(row)


def current_user(token):
    with store.connect() as con:return require(con,token)


def login(username, password):
    # Generic errors avoid revealing whether an account exists.
    username=(username or '').strip().casefold()[:128]
    now=int(time.time())
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        limit=con.execute('SELECT * FROM login_limits WHERE username=?',(username,)).fetchone()
        if limit and limit['blocked_until']>now:
            raise AuthenticationError('登录暂时受限，请稍后再试。')
        row=con.execute('SELECT * FROM accounts WHERE username=?',(username,)).fetchone()
        # A fixed-format dummy digest keeps unknown usernames on the same KDF path.
        dummy='scrypt$131072$8$1$'+'00'*16+'$'+'00'*32
        matched=verify_password(password,row['password_hash'] if row else dummy)
        if not row or not row['active'] or not matched:
            count=limit['failures']+1 if limit and now-limit['window_start']<LOGIN_WINDOW else 1
            start=limit['window_start'] if limit and now-limit['window_start']<LOGIN_WINDOW else now
            con.execute('INSERT INTO login_limits VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,window_start=excluded.window_start,blocked_until=excluded.blocked_until',
                (username,count,start,now+LOGIN_WINDOW if count>=MAX_FAILURES else 0))
            audit(con,None,'login_failed','accounts',None,{'username':username})
            # Persist throttle/audit before returning a generic error outside the transaction.
            token=None
        else:
            token=secrets.token_urlsafe(32)
            con.execute('DELETE FROM login_limits WHERE username=?',(username,))
            con.execute('DELETE FROM auth_sessions WHERE expires_at<=? OR last_seen<=?',(now,now-IDLE_SECONDS))
            con.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',
                (_token_hash(token),row['id'],now,now+SESSION_SECONDS,now))
            audit(con,row['id'],'login','accounts',row['id'])
    if token is None:raise AuthenticationError('账号或密码不正确。')
    return token


def logout(token):
    with store.connect() as con:
        user=require(con,token)
        con.execute('DELETE FROM auth_sessions WHERE token_hash=?',(_token_hash(token),))
        audit(con,user['id'],'logout','accounts',user['id'])


def create_account(token, username, name, password, role):
    # Check before doing expensive KDF, then recheck in the write transaction.
    with store.connect() as con:require(con,token,('system_admin',))
    encoded=password_hash(password)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user=require(con,token,('system_admin',))
        uid=_insert_account(con,username,name,encoded,role)
        audit(con,user['id'],'create_account','accounts',uid,{'role':role})
        return uid


def list_accounts(token):
    with store.connect() as con:
        require(con,token,('system_admin',))
        return [dict(r) for r in con.execute('SELECT id,username,display_name,role,coach_id,active FROM accounts ORDER BY id')]


def set_active(token, account_id, active):
    if not isinstance(active,bool):raise ValueError('启用状态不正确。')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user=require(con,token,('system_admin',))
        target=con.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone()
        if not target:raise ValueError('账号不存在。')
        if not active and target['role']=='system_admin' and target['active']:
            if con.execute("SELECT count(*) FROM accounts WHERE role='system_admin' AND active=1").fetchone()[0]<=1:
                raise ValueError('不能停用最后一个系统管理员。')
        con.execute('UPDATE accounts SET active=? WHERE id=?',(int(active),account_id))
        if not active:con.execute('DELETE FROM auth_sessions WHERE account_id=?',(account_id,))
        audit(con,user['id'],'set_account_active','accounts',account_id,{'active':active})


def change_password(token, current_password, new_password):
    encoded=password_hash(new_password)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        user=require(con,token)
        stored=con.execute('SELECT password_hash FROM accounts WHERE id=?',(user['id'],)).fetchone()[0]
        if not verify_password(current_password,stored):raise ValueError('当前密码不正确。')
        con.execute('UPDATE accounts SET password_hash=? WHERE id=?',(encoded,user['id']))
        con.execute('DELETE FROM auth_sessions WHERE account_id=?',(user['id'],))
        audit(con,user['id'],'change_password','accounts',user['id'])
