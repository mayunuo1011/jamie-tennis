"""Explicit least-privilege server role; credentials are written locally, never logged."""
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,quote,unquote
import argparse,json,os,secrets
import pg_store
from cloud_migrate import META

ROLE='jamie_runtime'


def provision(output):
    from psycopg import sql
    path=Path(output)
    if path.exists():raise ValueError('配置文件已存在，不会覆盖。')
    schema=pg_store.schema_name();password=secrets.token_urlsafe(36)
    params=pg_store.settings()
    admin_user=params.get('user','')
    # Session pooler usernames have role.PROJECT_REF; direct/local use the bare role.
    login_user=ROLE+'.'+admin_user.split('.',1)[1] if '.' in admin_user else ROLE
    host=params.get('host','');port=params.get('port','5432')
    if not host:raise ValueError('请使用明确的数据库主机。')
    sslmode=params.get('sslmode','disable' if host in ('127.0.0.1','localhost') else 'verify-full')
    netloc=quote(login_user,safe='')+':'+quote(password,safe='')+'@'+host+':'+str(port)
    dsn=urlunsplit(('postgresql',netloc,'/'+params.get('dbname','postgres'),'sslmode='+sslmode+('&sslrootcert=system' if sslmode=='verify-full' else ''),''))
    path.parent.mkdir(parents=True,exist_ok=True)
    created=False
    try:
        with path.open('x',encoding='utf-8') as f:
            created=True
            with pg_store.raw_connection() as con:
                if con.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone():raise ValueError('运行角色已存在，不重置密码；使用原配置或由维护人员核对。')
                con.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(ROLE),sql.Literal(password)))
                con.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(ROLE)))
                for table in META['tables']:
                    con.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(ROLE)))
                    con.execute(sql.SQL('CREATE POLICY server_runtime ON {}.{} TO {} USING (true) WITH CHECK (true)').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(ROLE)))
                con.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(ROLE)))
                f.write('JAMIE_DATABASE_URL = '+json.dumps(dsn)+'\nJAMIE_DB_SCHEMA = '+json.dumps(schema)+'\n')
                f.flush();os.fsync(f.fileno())
    except Exception:
        if created:path.unlink(missing_ok=True)
        raise
    return {'status':'created','role':ROLE,'secret_written_locally':True}


def main():
    parser=argparse.ArgumentParser(description='迁移完成后显式创建服务器专用账号；不会重置已有账号。')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    try:result=provision(args.output)
    except Exception:print('未完成，不会重置现有密码。请本机检查配置。');return 1
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
