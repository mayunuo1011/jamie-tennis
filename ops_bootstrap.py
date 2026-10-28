"""Local-only provisioning. No default credentials, no browser registration."""
import argparse
from getpass import getpass
import ops_store as store
import ops_auth as auth


def main():
    parser=argparse.ArgumentParser(description='建立独立预览数据库及首个系统管理员')
    parser.add_argument('--database',help='预览数据库路径，默认 data/jamie-preview.db')
    args=parser.parse_args()
    if args.database:store.configure(args.database)
    print('目标数据库：',store.DB_PATH)
    from ops_migrate3 import migrate
    saved=migrate()
    if saved:print('升级前备份：',saved)
    with store.connect() as con:
        existing=con.execute('SELECT 1 FROM accounts LIMIT 1').fetchone()
    if existing:
        print('管理员已存在，请使用工作台登录。不会重置或覆盖账号。')
        return
    username=input('管理员登录账号（如 systemadmin）：').strip()
    name=input('显示名称：').strip()
    password=getpass('管理员密码（15–128 字符，不会显示）：')
    if password!=getpass('再次输入密码：'):raise ValueError('两次密码不一致。')
    auth.bootstrap_admin(username,name,password)
    print('系统管理员创建成功。可以启动阶段一预览，登录后建立 Luo 和 Jamie 账号。')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError) as error:
        print('未完成：',error)
        raise SystemExit(1)
