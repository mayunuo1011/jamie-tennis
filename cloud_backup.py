"""Private local export and restore into an EMPTY replacement schema only."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json
import pg_store
import cloud_migrate as migration


def fingerprint(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def export(path):
    from psycopg import sql
    data={}
    with pg_store.raw_connection() as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        for table,columns in migration.META['tables'].items():
            if table in migration.SKIP:data[table]=[];continue
            order=[r['name'] for r in columns if r['pk']]
            statement=sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(sql.Identifier(r['name']) for r in columns),sql.Identifier(table))
            if order:statement+=sql.SQL(' ORDER BY {}').format(sql.SQL(',').join(map(sql.Identifier,order)))
            data[table]=[list(r.values) for r in raw.execute(statement)]
    payload={'format':'jamie-cloud-backup-v1','created_at':datetime.now(timezone.utc).isoformat(),'fingerprint':fingerprint(data),'tables':data}
    with Path(path).open('x',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False,separators=(',',':'))
    return {'status':'exported','rows':{t:len(v) for t,v in data.items()},'sessions_included':False}


def restore(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    if payload.get('format')!='jamie-cloud-backup-v1' or fingerprint(payload['tables'])!=payload.get('fingerprint'):raise ValueError('备份格式或完整性检查失败。')
    return migration.apply_data(payload['tables'],payload['fingerprint'])


def main():
    parser=argparse.ArgumentParser(description='备份含敏感信息，仅保存本机私密目录。恢复只允许空目标。')
    parser.add_argument('action',choices=['export','restore']);parser.add_argument('path',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.action=='restore' and not args.apply:print('恢复需要显式指定 --apply；目标必须为空。');return 1
    try:result=export(args.path) if args.action=='export' else restore(args.path)
    except Exception:print('未完成，请检查配置、备份文件以及目标是否为空；不会覆盖现有库。');return 1
    print(json.dumps(result,ensure_ascii=False));return 0


if __name__=='__main__':raise SystemExit(main())
