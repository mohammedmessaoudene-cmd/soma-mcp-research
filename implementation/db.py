"""Trusted host adapter. Roles and connection strings are never tool arguments."""
import json
import os
from pathlib import Path
import re

import psycopg

M = 2**53-1
MODEL_LIMIT = 1024
IDENT = re.compile(r'[A-Za-z0-9_-]{1,64}', re.ASCII)
ERRORS = {'INVALID_ARGUMENT', 'NOT_AUTHORIZED', 'VERSION_CONFLICT',
          'VERSION_EXHAUSTED', 'CAPACITY_EXCEEDED', 'BUDGET_EXHAUSTED',
          'IDEMPOTENCY_CONFLICT', 'STATE_INTEGRITY_ERROR'}


def config():
    return json.loads(Path(os.environ['SOMA_CONFIG']).read_text(encoding='utf-8'))


def connect(role='soma_alice', *, autocommit=False):
    cfg = config()
    if role not in cfg['passwords']:
        raise ValueError('Unknown host role')
    return psycopg.connect(host='127.0.0.1', port=cfg['port'], dbname=cfg['database'],
                           user=role, password=cfg['passwords'][role],
                           connect_timeout=5, autocommit=autocommit,
                           options='-c lock_timeout=3000 -c statement_timeout=10000')


def validate(tool, args):
    if type(args) is not dict:
        raise ValueError('INVALID_ARGUMENT')
    if len(json.dumps(args, allow_nan=False).encode())>4096:
        raise ValueError('INVALID_ARGUMENT')
    expected = {'soma.read_state': set(), 'soma.operation_status': {'operation_id'},
                'soma.adjust_allocation': {'operation_id', 'account_id', 'delta', 'expected_version'}}
    if tool not in expected or set(args)!=expected[tool]:
        raise ValueError('INVALID_ARGUMENT')
    for name in ('operation_id', 'account_id'):
        if name in args and (type(args[name]) is not str or not IDENT.fullmatch(args[name])):
            raise ValueError('INVALID_ARGUMENT')
    if tool=='soma.adjust_allocation':
        d, ev = args['delta'], args['expected_version']
        if type(d) is not int or type(ev) is not int or not -M<=d<=M or d==0 or not 0<=ev<=M:
            raise ValueError('INVALID_ARGUMENT')


def invoke(con, tool, args):
    validate(tool, args)
    if tool=='soma.read_state':
        return con.execute('SELECT soma.read_state()').fetchone()[0]
    if tool=='soma.operation_status':
        return con.execute('SELECT soma.operation_status(%s)', (args['operation_id'],)).fetchone()[0]
    return con.execute('SELECT soma.adjust_allocation(%s,%s,%s,%s)',
                       tuple(args[k] for k in ('operation_id','account_id','delta','expected_version'))).fetchone()[0]


def call(tool, args, role='soma_alice'):
    try:
        validate(tool,args)
        with connect(role) as con:
            result=invoke(con,tool,args)
        return result
    except (ValueError, TypeError, OverflowError):
        return {'status':'DENIED','code':'INVALID_ARGUMENT'}
    except psycopg.Error as exc:
        code=exc.diag.message_primary
        if code not in ERRORS:
            uncertain=isinstance(exc,psycopg.OperationalError) or (exc.sqlstate or '').startswith('08') or exc.sqlstate in ('55P03','57014','40001','40P01')
            code='OUTCOME_PENDING' if uncertain else 'STATE_INTEGRITY_ERROR'
        return {'status':'OUTCOME_PENDING' if code=='OUTCOME_PENDING' else 'DENIED','code':code}


def project(result):
    """The sole model-facing projection; _meta is retained separately by host."""
    public={k:v for k,v in result.items() if k!='feedback'}
    if result.get('status') in ('DENIED','OUTCOME_PENDING','NOT_OBSERVED_OR_PENDING'):
        public={k:result[k] for k in ('status','code','operation_id') if k in result}
        public['next_action']='RETRY_SAME_KEY_OR_QUERY' if result.get('code')=='OUTCOME_PENDING' or result['status']=='NOT_OBSERVED_OR_PENDING' else 'CORRECT_REQUEST_OR_STOP'
    encoded=json.dumps(public,sort_keys=True,separators=(',',':'),ensure_ascii=True)
    if len(encoded.encode('utf-8'))>MODEL_LIMIT:
        # Never silently truncate a receipt into a different asserted outcome.
        public={'status':'OUTCOME_PENDING','next_action':'QUERY_SAME_OPERATION_ID'}
        encoded=json.dumps(public,separators=(',',':'))
    return public,encoded


def model_result(result,facade='soma'):
    if facade not in ('soma','conventional'):
        raise ValueError('Unknown host facade')
    public,text=project(result)
    meta={'org.messaoudene.soma/feedback':result['feedback']} if facade=='soma' and 'feedback' in result else {}
    return public,text,meta
