"""Independent R4 snapshot oracle, including immutable receipt and event content.

Fixture initial account values are public and fixed. No SQL admission guard or
controller code is called. Privileged corruption detection is not a claim of
defending against the trusted database owner.
"""
import hashlib
import json

from psycopg.rows import dict_row
from db import connect,M


def canonical(value):
    # Python equality considers True==1 and 1.0==1. Exact JSON types matter here.
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def audit():
    with connect('soma_migration') as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        con.row_factory=dict_row
        tables={name:con.execute('SELECT * FROM soma.'+name).fetchall()
                for name in ('tenant_state','accounts','subjects','operations','reservations','outbox')}
    key=lambda row:(row['tenant_id'],row['principal_id'],row['operation_id'])
    operations={key(row):row for row in tables['operations']}
    reserves={key(row):row for row in tables['reservations']}
    events={key(row):row for row in tables['outbox']}
    assert len(operations)==len(tables['operations'])
    assert set(operations)==set(reserves)==set(events)
    assert len({e['event_id'] for e in events.values()})==len(events)
    subjects={(s['tenant_id'],s['principal_id']) for s in tables['subjects']}
    balances={(a['tenant_id'],a['account_id']):a['balance'] for a in tables['accounts']}
    rebuilt={k:(40 if k[1]=='allocation_a' else 50 if k[1]=='allocation_b' else 0) for k in balances}
    tenants=[]
    for t in sorted(tables['tenant_state'],key=lambda row:row['tenant_id']):
        tid=t['tenant_id'];rows=[o for o in operations.values() if o['tenant_id']==tid]
        rows.sort(key=lambda row:row['committed_version'])
        assert [o['committed_version'] for o in rows]==list(range(1,t['version']+1))
        actual=[a['balance'] for a in tables['accounts'] if a['tenant_id']==tid]
        assert all(type(v) is int and 0<=v<=M for v in actual)
        assert sum(actual)==t['total'] and 0<=t['total']<=t['cap']<=M
        rs=[r for r in reserves.values() if r['tenant_id']==tid]
        assert t['spent']==sum(r['actual_cost'] for r in rs if r['state']=='SETTLED')
        assert t['held']==sum(r['upper_bound'] for r in rs if r['state']=='HELD')
        assert t['unknown']==sum(r['upper_bound'] for r in rs if r['state']=='UNKNOWN')
        assert 0<=t['spent']+t['held']+t['unknown']<=t['budget_limit']<=M
        for o in rows:
            k=key(o);r=reserves[k];e=events[k]
            assert k[:2] in subjects
            assert o['tool_version']=='r3-1'
            assert type(o['delta']) is int and o['delta']!=0 and abs(o['delta'])<=M
            assert o['committed_version']==o['expected_version']+1
            assert 1<=o['admitted_policy_version']<=t['policy_version']
            account=(tid,o['account_id']);assert account in rebuilt
            rebuilt[account]+=o['delta'];assert rebuilt[account]>=0
            eid=':'.join(k)
            assert e['event_id']==eid
            receipt=dict(status='COMMITTED',event_id=eid,operation_id=o['operation_id'],
                committed_version=o['committed_version'],admitted_policy_version=o['admitted_policy_version'],delivery_status_at_commit='READY')
            assert canonical(o['result_payload'])==canonical(receipt),('RECEIPT_CONTENT',k)
            body=json.loads(e['payload'])
            assert canonical(body)==canonical(dict(event_id=eid,account_id=o['account_id'],delta=o['delta'],committed_version=o['committed_version'])),('PAYLOAD_CONTENT',k)
            assert hashlib.sha256(e['payload'].encode('utf-8')).hexdigest()==e['payload_hash'],('PAYLOAD_HASH',k)
            assert r['upper_bound']==60 and 0<=e['attempt_count']<=3
            assert (r['state']=='SETTLED')==(r['actual_cost'] is not None)==(e['delivery_state']=='ACKED')
            if r['state']=='SETTLED':assert 0<=r['actual_cost']<=r['upper_bound']
            if r['state']=='HELD':assert e['delivery_state'] in ('READY','IN_FLIGHT')
            if r['state']=='UNKNOWN':assert e['delivery_state'] in ('UNKNOWN','IN_FLIGHT')
        tenants.append(tuple(t[k] for k in ('tenant_id','total','cap','version','budget_limit','spent','held','unknown')))
    assert rebuilt==balances, 'EFFECT_LEDGER_RECONSTRUCTION'
    return dict(tenants=tenants,operations=len(operations),reservations=len(reserves),outbox=len(events),
                receipt_payload_integrity=True,effect_ledger_reconstruction=True,status='PASS')
