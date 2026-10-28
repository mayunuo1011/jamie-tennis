"""Create a new, fictional preview only. Never overwrite an existing DB."""
from datetime import datetime, time, timedelta
import secrets

import ops_store as store
import ops_auth as auth
from user_service import week_start


def create_demo():
    if store.DB_PATH.exists():raise ValueError('预览数据库已存在，不会覆盖。请继续使用现有账号。')
    from ops_migrate3 import migrate
    migrate()
    passwords={name:secrets.token_urlsafe(18) for name in ('systemadmin','luo','jamie')}
    admin=auth.bootstrap_admin('systemadmin','System Admin',passwords['systemadmin'])
    token=auth.login('systemadmin',passwords['systemadmin'])
    luo=auth.create_account(token,'luo','Luo',passwords['luo'],'coach')
    jamie=auth.create_account(token,'jamie','Jamie',passwords['jamie'],'operator')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        ids={r['id']:r['coach_id'] for r in con.execute('SELECT id,coach_id FROM accounts')}
        sid=con.execute('''INSERT INTO students(name,source,level,goals,joined_at)
           VALUES('虚拟学员 Alpha','朋友推荐','入门','提高击球稳定性',?)''',(week_start().isoformat(),)).lastrowid
        for index,uid in enumerate((luo,jamie)):
            start=datetime.combine(week_start()+timedelta(days=1),time(14+index))
            lid=con.execute('''INSERT INTO lessons(student_id,coach_id,kind,starts_at,ends_at,location,note,next_goal,checkin_done)
              VALUES(?,?,?,?,?,?,?,?,1)''',(sid,ids[uid],'私教',start.isoformat(timespec='minutes'),
                (start+timedelta(hours=1)).isoformat(timespec='minutes'),'Meadows' if uid==luo else 'Grange Club',
                f'{"Luo" if uid==luo else "Jamie"} 的虚拟训练记录，今天练习正手动作与脚步配合。','下次目标是提高击球稳定性并继续练习脚步移动。')).lastrowid
            from ops_reviews import submit_current
            submit_current(con,lid,uid)
            con.execute('INSERT INTO student_coaches VALUES(?,?)',(sid,ids[uid]))
        auth.audit(con,admin,'create_fictional_demo','students',sid)
    auth.logout(token)
    return passwords


if __name__=='__main__':
    try:
        passwords=create_demo()
        print('独立虚拟预览已创建，没有导入真实学员或课包。')
        print('以下密码每次随机生成，请保存好，不会写入明文密码文件：')
        for username,password in passwords.items():print(username,':',password)
        print('双击“启动阶段一预览.bat”打开登录页。')
    except (ValueError,RuntimeError) as error:
        print('未创建：',error)
        raise SystemExit(1)
