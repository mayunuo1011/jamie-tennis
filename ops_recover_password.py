"""Local maintenance only. Never expose this recovery operation through the Web UI."""
from getpass import getpass, getuser
import sqlite3

import ops_auth as auth
import ops_store as store


def require_existing_database():
    if not store.DB_PATH.is_file():
        raise ValueError('没有找到预览数据库。请把工具放到 ops_app.py 所在文件夹，不会创建新数据库。')
    with store.connect() as con:
        ready=con.execute("SELECT 1 FROM sqlite_master WHERE name='ops_migrations'").fetchone()
        if not ready or not con.execute('SELECT 1 FROM ops_migrations WHERE version=1').fetchone():
            raise ValueError('这不是阶段一预览数据库，未修改。')


def recover_password(username, new_password):
    """OS-level local recovery: audit it and revoke all target account sessions."""
    require_existing_database()
    username=auth._username(username)
    with store.connect() as con:
        target=con.execute('SELECT id FROM accounts WHERE username=?',(username,)).fetchone()
        if not target:raise ValueError('账号不存在，未修改。')
    encoded=auth.password_hash(new_password)
    saved=store.backup()
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        target=con.execute('SELECT id FROM accounts WHERE username=?',(username,)).fetchone()
        if not target:raise ValueError('账号不存在，未修改。')
        con.execute('UPDATE accounts SET password_hash=? WHERE id=?',(encoded,target['id']))
        con.execute('DELETE FROM auth_sessions WHERE account_id=?',(target['id'],))
        # No authenticated Web actor: do not impersonate the account being recovered.
        auth.audit(con,None,'local_password_recovery','accounts',target['id'],
            {'method':'local_cli','os_user':getuser(),'username':username})
    return saved


def main():
    require_existing_database()
    print('Local password recovery / 本机密码重设')
    print('数据库：',store.DB_PATH)
    with store.connect() as con:
        for row in con.execute('SELECT username,display_name FROM accounts ORDER BY id'):
            print('账号：',row['username'],'  显示名称：',row['display_name'])
    username=input('要重设的账号（直接回车默认 systemadmin）：').strip() or 'systemadmin'
    password=getpass('新密码（15–128 字符，输入不会显示）：')
    if password!=getpass('再次输入新密码：'):
        raise ValueError('两次密码不一致，未修改。')
    saved=recover_password(username,password)
    print('密码重设成功。请使用这个账号和你刚设置的新密码登录。')
    print('原有课程、学员和账号权限均保留；该账号之前的登录已退出。')
    print('修改前备份：',saved)


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,sqlite3.Error) as error:
        print('未完成：',error)
        raise SystemExit(1)
    except (KeyboardInterrupt,EOFError):
        print('\n已取消，未重设密码。')
        raise SystemExit(1)
