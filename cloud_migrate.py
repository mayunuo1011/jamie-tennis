"""Explicit, atomic SQLite-7 to PostgreSQL import. Default is read-only planning.
Run only against a backup approved for cloud transfer. Never resets a target.
"""
from pathlib import Path
from contextlib import closing,contextmanager
import argparse,hashlib,json,sqlite3,tempfile,os

SKIP={'auth_sessions','student_sessions','login_limits','student_login_limits'}
META=json.loads(Path(__file__).with_name('cloud_schema.json').read_text(encoding='utf-8'))


@contextmanager
def snapshot(path):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('找不到待迁移数据库，不会创建空数据库。')
    with tempfile.TemporaryDirectory(prefix='jamie-migration-') as folder:
        dest=Path(folder)/'snapshot.db'
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(dest)) as copy:src.backup(copy)
        with closing(sqlite3.connect(dest)) as con:
            con.row_factory=sqlite3.Row
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('源数据库完整性检查失败。')
            versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
            if versions!=set(range(1,8)):raise ValueError('只支持已验证的版本7数据库。')
            for table,expected in META['tables'].items():
                actual=[r['name'] for r in con.execute('PRAGMA table_info("'+table+'")')]
                if actual!=[r['name'] for r in expected]:raise ValueError('源表结构不匹配：'+table)
            if not con.execute("SELECT 1 FROM accounts WHERE role='system_admin' AND active=1").fetchone():raise ValueError('源数据库缺少有效系统管理员。')
            yield con


def content(con):
    tables={}
    for table in META['tables']:
        if table in SKIP:tables[table]=[];continue
        columns=META['tables'][table]
        order=','.join('"'+r['name']+'"' for r in columns if r['pk'])
        rows=con.execute('SELECT * FROM "'+table+'"'+(' ORDER BY '+order if order else '')).fetchall()
        tables[table]=[tuple(row) for row in rows]
    digest=hashlib.sha256(json.dumps(tables,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return tables,digest


def plan(source):
    with snapshot(source) as con:
        data,digest=content(con)
    return {'schema':7,'fingerprint':digest,'rows':{t:len(rows) for t,rows in data.items()},'sessions_migrated':False}


def initialize_empty(raw,schema):
    from psycopg import sql
    # An existing schema is only accepted if truly empty, never DROP or TRUNCATE.
    exists=raw.execute('SELECT 1 FROM information_schema.schemata WHERE schema_name=%s',(schema,)).fetchone()
    if exists:
        if raw.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=%s',(schema,)).fetchone():raise ValueError('目标已有数据表，停止覆盖。')
    else:raw.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    raw.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(schema)))
    raw.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
    for statement in Path(__file__).with_name('cloud_schema.sql').read_text(encoding='utf-8').split(';'):
        if statement.strip():raw.execute(statement)
    raw.execute('CREATE TABLE cloud_imports (fingerprint TEXT PRIMARY KEY, imported_at TIMESTAMPTZ NOT NULL DEFAULT now(), counts TEXT NOT NULL)')
    for table in META['tables']:
        raw.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table)))
    # Dedicated schema is not exposed through Supabase Data API; no anon grants.


def apply(source):
    from psycopg import sql
    import pg_store
    with snapshot(source) as src:
        data,digest=content(src)
    return apply_data(data,digest)


def apply_data(data,digest):
    from psycopg import sql
    import pg_store
    if hashlib.sha256(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()!=digest:raise ValueError('迁移内容校验失败。')
    if set(data)!=set(META['tables']):raise ValueError('备份数据表清单不匹配。')
    for table,rows in data.items():
        if any(len(r)!=len(META['tables'][table]) for r in rows):raise ValueError('备份字段数量不匹配。')
        if table in SKIP and rows:raise ValueError('不能恢复旧登录会话。')
    if {r[0] for r in data['ops_migrations']}!=set(range(1,8)):raise ValueError('备份版本不匹配。')
    cols=[r['name'] for r in META['tables']['accounts']]
    if not any(r[cols.index('role')]=='system_admin' and r[cols.index('active')]==1 for r in data['accounts']):raise ValueError('备份缺少有效管理员。')
    schema=pg_store.schema_name()
    with pg_store.raw_connection() as raw:
        raw.execute('SELECT pg_advisory_xact_lock(%s)',(pg_store.WRITE_LOCK,))
        exists=raw.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='cloud_imports'",(schema,)).fetchone()
        if exists:
            cached=raw.execute(sql.SQL('SELECT fingerprint FROM {}.cloud_imports').format(sql.Identifier(schema))).fetchone()
            if cached and cached[0]==digest:return {'status':'already_imported','rows':{t:len(rows) for t,rows in data.items()}}
            raise ValueError('目标已有迁移记录，不会覆盖或合并现有数据。')
        initialize_empty(raw,schema)
        raw.execute('SET CONSTRAINTS ALL DEFERRED')
        for table,rows in data.items():
            if not rows:continue
            columns=[r['name'] for r in META['tables'][table]]
            statement=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
            with raw.cursor() as cursor:cursor.executemany(statement,rows)
        for table in data:
            columns=[r['name'] for r in META['tables'][table]]
            order=[r['name'] for r in META['tables'][table] if r['pk']]
            statement=sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table))
            if order:statement+=sql.SQL(' ORDER BY {}').format(sql.SQL(',').join(map(sql.Identifier,order)))
            copied=[tuple(r.values) for r in raw.execute(statement)]
            if copied!=[tuple(row) for row in data[table]]:raise ValueError('迁移后内容核对失败：'+table)
            if table in pg_store.IDENTITY_TABLES:
                seq=raw.execute('SELECT pg_get_serial_sequence(%s,%s)',(schema+'.'+table,'id')).fetchone()[0]
                highest=max((row[columns.index('id')] for row in data[table]),default=0)
                raw.execute('SELECT setval(%s,%s,%s)',(seq,max(1,highest),bool(highest)))
        counts={t:len(rows) for t,rows in data.items()}
        raw.execute('INSERT INTO cloud_imports(fingerprint,counts) VALUES(%s,%s)',(digest,json.dumps(counts)))
        raw.execute('SET CONSTRAINTS ALL IMMEDIATE')
    return {'status':'imported','schema':7,'rows':counts,'sessions_migrated':False}


def main():
    parser=argparse.ArgumentParser(description='默认只核查，--apply才上传到配置的独立云端数据库。')
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:result=apply(args.source) if args.apply else plan(args.source)
    except Exception:
        print('迁移未完成。未覆盖已有目标；请检查连接配置、源版本及目标是否为空。详细诊断应只在本机查看，不分享密钥。');return 1
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
