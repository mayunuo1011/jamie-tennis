"""Local setup assistant. Secrets stay in .cloud-private, never in source control."""
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,quote,unquote
import argparse,getpass,json,os,tomllib

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'.cloud-private'


def load_admin():
    config=tomllib.loads((PRIVATE/'admin.toml').read_text(encoding='utf-8'))
    os.environ['JAMIE_DATABASE_URL']=config['JAMIE_DATABASE_URL']
    os.environ['JAMIE_DB_SCHEMA']=config.get('JAMIE_DB_SCHEMA','jamie_tennis')


def configure():
    print('从 Supabase 的 Connect 里选择 Session pooler (端口5432)，复制 URI。')
    print('输入只保存在本机私密配置，不要把内容发到聊天或GitHub。')
    uri=getpass.getpass('粘贴连接URI（隐藏输入）：').strip()
    parts=urlsplit(uri)
    if parts.scheme not in ('postgres','postgresql') or not parts.hostname or not parts.username:raise ValueError('连接格式不正确。')
    password=unquote(parts.password or '')
    if not password or 'YOUR-PASSWORD' in password:password=getpass.getpass('数据库密码（隐藏输入）：')
    if not password:raise ValueError('密码不能为空。')
    port=parts.port or 5432
    if port!=5432:raise ValueError('请选择Session pooler的5432端口。')
    netloc=quote(unquote(parts.username),safe='')+':'+quote(password,safe='')+'@'+parts.hostname+':'+str(port)
    dsn=urlunsplit(('postgresql',netloc,parts.path or '/postgres','sslmode=verify-full&sslrootcert=system',''))
    PRIVATE.mkdir(exist_ok=True)
    path=PRIVATE/'admin.toml'
    with path.open('x',encoding='utf-8') as f:f.write('JAMIE_DATABASE_URL = '+json.dumps(dsn)+'\nJAMIE_DB_SCHEMA = "jamie_tennis"\n')
    print('本机配置已保存；没有迁移任何数据。')


def main():
    parser=argparse.ArgumentParser(description='本机云端准备工具，不修改现用网站。')
    parser.add_argument('action',choices=['configure','check','plan','import','runtime'])
    parser.add_argument('--source',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        if args.action=='configure':configure();return 0
        if args.action=='plan':
            if not args.source:raise ValueError('需要指定数据库备份。')
            import cloud_migrate
            print(json.dumps(cloud_migrate.plan(args.source),ensure_ascii=False,indent=2));return 0
        load_admin()
        import pg_store
        if args.action=='check':
            with pg_store.raw_connection() as con:con.execute('SELECT 1')
            print('云端连接成功；未上传业务数据。')
        elif args.action=='import':
            if not args.apply or not args.source:raise ValueError('上传必须明确提供--apply和已确认的数据库备份路径。')
            import cloud_migrate
            print(json.dumps(cloud_migrate.apply(args.source),ensure_ascii=False))
        else:
            if not args.apply:raise ValueError('创建服务器专用账号需要显式--apply。')
            import cloud_runtime
            cloud_runtime.provision(PRIVATE/'streamlit-secrets.toml')
            print('服务器专用配置已保存在 .cloud-private/streamlit-secrets.toml；只粘贴到Streamlit Secrets，不能上传GitHub。')
        return 0
    except Exception:
        print('未完成。请检查本机设置、操作选项和目标；不会覆盖已有配置或重置现用网站。');return 1


if __name__=='__main__':raise SystemExit(main())
