"""Administrative synthetic fixture creation, only inside a disposable cluster."""
from db import connect


def reset(n=128,cap=100,budget=100):
    with connect('soma_migration') as con:
        con.execute('TRUNCATE soma.outbox,soma.reservations,soma.operations,soma.subjects,soma.accounts,soma.tenant_state')
        con.execute("INSERT INTO soma.tenant_state(tenant_id,total,cap,budget_limit) VALUES('A',90,%s,%s),('B',90,%s,%s)",(cap,budget,cap,budget))
        with con.cursor() as cur:
            for tenant in ('A','B'):
                cur.executemany('INSERT INTO soma.accounts VALUES(%s,%s,%s)',
                    [(tenant,'allocation_a',40),(tenant,'allocation_b',50)]+
                    [(tenant,f'account_{i}',0) for i in range(n-2)])
        con.execute("INSERT INTO soma.subjects(db_role,principal_id,tenant_id) VALUES('soma_alice','alice','A'),('soma_peer','peer','A'),('soma_bob','bob','B')")
    initial_audit=audit()
    return {'accounts_per_tenant':n,'cap':cap,'budget':budget,'initial_total':90,'initial_audit':initial_audit['status']}


def audit():
    """Independent full-scan oracle in one consistent snapshot, no admission helper."""
    with connect('soma_migration') as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        tenants=con.execute('SELECT tenant_id,total,cap,version,budget_limit,spent,held,unknown FROM soma.tenant_state ORDER BY tenant_id').fetchall()
        accounts=con.execute('SELECT tenant_id,account_id,balance FROM soma.accounts ORDER BY tenant_id,account_id').fetchall()
        ops=con.execute('SELECT tenant_id,principal_id,operation_id,committed_version FROM soma.operations ORDER BY tenant_id,committed_version').fetchall()
        reserves=con.execute('SELECT tenant_id,principal_id,operation_id,upper_bound,actual_cost,state FROM soma.reservations').fetchall()
        events=con.execute('SELECT tenant_id,principal_id,operation_id,delivery_state FROM soma.outbox').fetchall()
    for tid,total,cap,version,budget,spent,held,unknown in tenants:
        balances=[b for t,a,b in accounts if t==tid]
        selected=[o for o in ops if o[0]==tid]
        rs=[r for r in reserves if r[0]==tid]
        assert all(type(b) is int and 0<=b<=2**53-1 for b in balances)
        assert sum(balances)==total and 0<=total<=cap<=2**53-1
        assert version==len(selected)
        assert [o[3] for o in selected]==list(range(1,version+1))
        assert spent==sum(r[4] for r in rs if r[5]=='SETTLED')
        assert held==sum(r[3] for r in rs if r[5]=='HELD')
        assert unknown==sum(r[3] for r in rs if r[5]=='UNKNOWN')
        assert 0<=spent+held+unknown<=budget<=2**53-1
    keys=lambda rows:{tuple(r[:3]) for r in rows}
    assert keys(ops)==keys(reserves)==keys(events)
    assert len(ops)==len(reserves)==len(events)
    bykey={tuple(r[:3]):r[5] for r in reserves}
    assert all((e[3]=='ACKED')==(bykey[tuple(e[:3])]=='SETTLED') for e in events)
    return {'tenants':tenants,'operations':len(ops),'reservations':len(reserves),'outbox':len(events),'status':'PASS'}
