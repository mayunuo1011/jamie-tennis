"""PostgreSQL storage boundary for existing parameterized SQLite-style services.
Only the few SQL forms used by this app are adapted. Business rules stay shared.
"""
from contextlib import contextmanager
from collections.abc import Mapping
import os,re
import sqlite3
from pathlib import Path

IDENTITY_TABLES={'students','packages','lessons','coaches','accounts','ops_audit','feedback_submissions','credit_ledger','group_reminders','booking_slots'}
WRITE_LOCK=20898765586123091


class Row(Mapping):
    def __init__(self,names,values):self.names=tuple(names);self.values=tuple(values);self.mapping=dict(zip(names,values))
    def __getitem__(self,key):return self.values[key] if isinstance(key,(int,slice)) else self.mapping[key]
    def __iter__(self):return iter(self.names)
    def __len__(self):return len(self.names)
    def keys(self):return self.names


def row_factory(cursor):
    names=[d.name for d in (cursor.description or [])]
    return lambda values:Row(names,values)


def schema_name():
    value=os.environ.get('JAMIE_DB_SCHEMA','jamie_tennis')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,40}',value):raise RuntimeError('数据库配置无效。')
    if value in ('public','auth','storage','extensions','information_schema') or value.startswith('pg_'):raise RuntimeError('请使用独立业务数据库区域。')
    return value


def settings():
    dsn=os.environ.get('JAMIE_DATABASE_URL','')
    if not dsn:raise RuntimeError('尚未配置云端数据库连接。')
    from psycopg.conninfo import conninfo_to_dict
    try:params=conninfo_to_dict(dsn)
    except Exception:raise RuntimeError('数据库连接配置无效。') from None
    local=params.get('host') in ('127.0.0.1','localhost','::1')
    if not local:
        if params.get('sslmode','verify-full')!='verify-full':raise RuntimeError('云端数据库必须验证加密连接。')
        params['sslmode']='verify-full';params.setdefault('sslrootcert','system')
        if params.get('host','').endswith('.pooler.supabase.com') and params['sslrootcert']=='system':
            params['sslrootcert']=str(Path(__file__).with_name('supabase-ca.crt'))
    params.setdefault('connect_timeout','10')
    return params


def prepare(statement):
    sql=statement.strip().rstrip(';')
    if sql.upper()=='BEGIN IMMEDIATE':return 'SELECT pg_advisory_xact_lock('+str(WRITE_LOCK)+')',None
    if re.match(r'^(PRAGMA|ATTACH|DETACH|VACUUM)\b',sql,re.I):raise RuntimeError('SQLite本地维护命令不能用于云端。')
    if 'sqlite_master' in sql:raise RuntimeError('请使用云端维护工具。')
    ignored=bool(re.match(r'INSERT\s+OR\s+IGNORE\b',sql,re.I))
    sql=re.sub(r'INSERT\s+OR\s+IGNORE\b','INSERT',sql,flags=re.I)
    sql=re.sub(r'([\w.]+)\s*=\s*\?\s+COLLATE\s+NOCASE',r'lower(\1)=lower(?)',sql,flags=re.I)
    if re.search(r'COLLATE\s+NOCASE',sql,re.I):raise RuntimeError('未支持的大小写查询。')
    sql=re.sub(r'\?\s+IS\s+NULL','CAST(? AS text) IS NULL',sql,flags=re.I)
    if ignored:sql+=' ON CONFLICT DO NOTHING'
    match=re.match(r'INSERT\s+INTO\s+["`]?([a-z_]+)',sql,re.I)
    identity=match.group(1).lower() if match and match.group(1).lower() in IDENTITY_TABLES else None
    if identity and not re.search(r'\bRETURNING\b',sql,re.I):sql+=' RETURNING id'
    # Tokenize literals: question marks in text must remain text. psycopg escapes %.
    output=[];quoted=False;i=0
    while i<len(sql):
        char=sql[i]
        if char=="'":
            output.append(char)
            if quoted and i+1<len(sql) and sql[i+1]=="'":output.append("'");i+=2;continue
            quoted=not quoted
        elif char=='?' and not quoted:output.append('%s')
        elif char=='%':output.append('%%')
        else:output.append(char)
        i+=1
    return ''.join(output),identity


class Cursor:
    def __init__(self,cursor,identity=False):
        self.cursor=cursor;self.rowcount=cursor.rowcount;self.lastrowid=None
        if identity:
            row=cursor.fetchone();self.lastrowid=row[0] if row else None
    def fetchone(self):return self.cursor.fetchone()
    def fetchall(self):return self.cursor.fetchall()
    def __iter__(self):return iter(self.cursor)


class Connection:
    def __init__(self,raw):self.raw=raw
    def execute(self,statement,values=()):
        sql,identity=prepare(statement)
        try:return Cursor(self.raw.execute(sql,tuple(values),prepare=False),bool(identity))
        except Exception as error:
            import psycopg
            if isinstance(error,psycopg.IntegrityError):raise sqlite3.IntegrityError('数据约束检查未通过，操作已回滚。') from None
            raise
    def executemany(self,statement,values):
        for row in values:self.execute(statement,row)
    def commit(self):self.raw.commit()
    def rollback(self):self.raw.rollback()


@contextmanager
def raw_connection():
    import psycopg
    from psycopg import sql
    params=settings();schema=schema_name()
    raw=None
    try:
        raw=psycopg.connect(**params,row_factory=row_factory,prepare_threshold=None)
        raw.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
        raw.execute("SET LOCAL statement_timeout = '20s'")
        raw.execute("SET LOCAL lock_timeout = '15s'")
        yield raw
        raw.commit()
    except Exception:
        if raw is not None:raw.rollback()
        raise
    finally:
        if raw is not None:raw.close()


@contextmanager
def connect():
    try:
        with raw_connection() as raw:yield Connection(raw)
    except (ValueError,RuntimeError,sqlite3.Error):raise
    except Exception as error:
        import psycopg
        if isinstance(error,psycopg.Error):raise RuntimeError('云端数据库暂不可用，操作未完成，请稍后重试。') from None
        raise
