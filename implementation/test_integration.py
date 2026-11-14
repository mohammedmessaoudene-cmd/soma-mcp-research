"""Risk-directed real PostgreSQL, real process and official MCP acceptance tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import itertools
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import psycopg

from db import M,call,connect,invoke,project
from fixtures import reset,audit
from worker import deliver,transition,receiver_call

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
DETAILS={}


def req(op='one',delta=5,version=0,account='allocation_a'):
    return dict(operation_id=op,account_id=account,delta=delta,expected_version=version)


def adjust(op='one',delta=5,version=0,role='soma_alice',account='allocation_a'):
    return call('soma.adjust_allocation',req(op,delta,version,account),role)


def run_process(script,args,**kw):
    return subprocess.run([sys.executable,str(HERE/script),*map(str,args)],text=True,
                          capture_output=True,timeout=20,**kw)


def wait_marker(process,path):
    deadline=time.monotonic()+10
    while not path.exists() and time.monotonic()<deadline:
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        time.sleep(.01)
    assert path.exists(), 'Process failed to reach crash barrier'


def kill_attested_holder(process,marker,verify_sql=False):
    """Windows venv python may be a launcher; terminate the attested interpreter."""
    holder=marker['process_pid']
    assert type(holder) is int and holder>0 and holder!=os.getpid()
    assert process.poll() is None
    if verify_sql:
        with connect('soma_migration') as con:
            row=con.execute('SELECT usename FROM pg_stat_activity WHERE pid=%s',(marker['backend_pid'],)).fetchone()
            assert row==('soma_alice',),row
    os.kill(holder,signal.SIGTERM)
    process.communicate(timeout=10)
    assert process.returncode!=0
    if verify_sql:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with connect('soma_migration') as con:
                alive=con.execute('SELECT 1 FROM pg_stat_activity WHERE pid=%s',(marker['backend_pid'],)).fetchone()
            if not alive:break
            time.sleep(.01)
        else:raise AssertionError('Terminated SQL holder backend still present')
    return dict(launcher_pid=process.pid,holder_pid=holder,termination='os.kill(SIGTERM)',backend_disappeared=bool(verify_sql))


def wait_blocked(fragment):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with connect('soma_migration') as con:
            rows=con.execute("SELECT pid,wait_event_type,query FROM pg_stat_activity WHERE usename='soma_alice' AND wait_event_type='Lock'").fetchall()
        if any(fragment in row[2] for row in rows):
            return rows
        time.sleep(.01)
    raise AssertionError('No observed PostgreSQL lock wait')


def parallel(functions):
    barrier=threading.Barrier(len(functions))
    def run(f):
        barrier.wait(timeout=10)
        return f()
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures=[pool.submit(run,f) for f in functions]
        return [f.result(timeout=30) for f in futures]


class Integration(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp=tempfile.TemporaryDirectory(prefix='soma_r3_')
        self.folder=Path(self.tmp.name)
        self.receiver=self.folder/'receiver.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def assert_denied(self,result,code):
        self.assertEqual(result,{'status':'DENIED','code':code})

    def test_g02_strict_types_and_no_partial_effect(self):
        before=audit()
        invalid=[True,False,1.0,1.5,'1',M+1,-M-1,0,None,float('nan')]
        for value in invalid:
            self.assert_denied(call('soma.adjust_allocation',req(delta=value)),'INVALID_ARGUMENT')
        for value in [True,1.0,'0',-1,M+1,None]:
            self.assert_denied(call('soma.adjust_allocation',req(version=value)),'INVALID_ARGUMENT')
        for item in [dict(req(),tenant='B'),dict(req(),claimed_safe=True),
                     dict(req(),principal='bob'),dict(req(),cost=0),dict(req(),operation_id='é'),
                     dict(req(),operation_id='x'*65),dict(req(),account_id='../a'),{},[],None]:
            self.assert_denied(call('soma.adjust_allocation',item),'INVALID_ARGUMENT')
        self.assertEqual(before,audit())
        with connect() as con:
            with self.assertRaises(psycopg.Error):
                con.execute("SELECT soma.adjust_allocation('bad','allocation_a',0,0)")
        with connect('soma_migration') as con:
            with self.assertRaises(psycopg.errors.CheckViolation):
                con.execute("UPDATE soma.accounts SET balance=-1 WHERE tenant_id='A'")
        self.assertEqual(before,audit())

    def test_g03_host_identity_and_private_receipts(self):
        r=adjust()
        self.assertEqual(r['status'],'COMMITTED')
        self.assertEqual(call('soma.operation_status',{'operation_id':'one'},'soma_peer')['status'],'NOT_OBSERVED_OR_PENDING')
        self.assertEqual(call('soma.operation_status',{'operation_id':'one'},'soma_bob')['status'],'NOT_OBSERVED_OR_PENDING')
        with connect() as con:
            con.execute("SET soma.tenant='B'")
            self.assertEqual(invoke(con,'soma.read_state',{})['total'],95)
            self.assertEqual(con.execute('SELECT session_user,current_user').fetchone(),('soma_alice','soma_alice'))
        self.assertEqual(call('soma.read_state',{},'soma_bob')['total'],90)
        self.assert_denied(adjust('cross',1,1,account='bob_only'),'NOT_AUTHORIZED')
        self.assertEqual(audit()['operations'],1)

    def test_g03_permissions_revoke_before_replay(self):
        adjust()
        with connect('soma_migration') as con:
            con.execute("SELECT soma.set_rights('soma_alice',true,true,true,false)")
        self.assert_denied(call('soma.operation_status',{'operation_id':'one'}),'NOT_AUTHORIZED')
        self.assert_denied(adjust(),'NOT_AUTHORIZED')
        self.assertEqual(call('soma.read_state',{})['version'],1)
        with connect('soma_migration') as con:
            con.execute("SELECT soma.set_rights('soma_alice',false,true,true,true)")
        for tool,args in [('soma.read_state',{}),('soma.adjust_allocation',req()),('soma.operation_status',{'operation_id':'one'})]:
            self.assert_denied(call(tool,args),'NOT_AUTHORIZED')
        self.assertEqual(audit()['operations'],1)

    def test_g03_permission_matrix_and_feedback_on_every_tool(self):
        rows=[]
        for rd,mu,rc in itertools.product((False,True),repeat=3):
            reset(cap=1000,budget=10000);adjust('historical')
            with connect('soma_migration') as con:
                con.execute("SELECT soma.set_rights('soma_alice',true,%s,%s,%s)",(rd,mu,rc))
            state=call('soma.read_state',{})
            status=call('soma.operation_status',{'operation_id':'historical'})
            replay=adjust('historical')
            mutation=adjust('new',1,1)
            self.assertEqual(state.get('status')=='OBSERVED',rd)
            self.assertEqual(status.get('status')=='COMMITTED',rc)
            self.assertEqual(replay.get('status')=='COMMITTED',rc)
            self.assertEqual(mutation.get('status')=='COMMITTED',mu)
            for result in (state,status,replay,mutation):
                if not rd:
                    self.assertNotIn('feedback',result)
                    self.assertNotIn('observation_version',result)
            if mu and not rc:
                self.assertNotIn('event_id',mutation)
                self.assertNotIn('admitted_policy_version',mutation)
                self.assertEqual(set(mutation)-{'feedback'}, {'status','operation_id','replayed'})
            rows.append(dict(read_state=rd,mutate=mu,read_receipt=rc,results=dict(state=state,status=status,replay=replay,mutation=mutation)))
            audit()
        DETAILS['permission_matrix']=rows

    def test_g03_concurrent_revocation_linearizes(self):
        with connect('soma_migration') as admin:
            admin.execute("SELECT 1 FROM soma.tenant_state WHERE tenant_id='A' FOR UPDATE")
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(adjust)
                waits=wait_blocked('adjust_allocation')
                admin.execute("SELECT soma.set_rights('soma_alice',false,true,true,true)")
                admin.commit()
                self.assert_denied(future.result(timeout=10),'NOT_AUTHORIZED')
        DETAILS['revocation_lock_waits']=waits
        self.assertEqual(audit()['operations'],0)

    def test_g04_storage_privileges_and_shadowing(self):
        attacks=["UPDATE soma.accounts SET balance=0", "INSERT INTO soma.accounts VALUES('A','pwn',0)",
          "DELETE FROM soma.operations", "UPDATE soma.tenant_state SET total=0",
          "ALTER TABLE soma.accounts DISABLE TRIGGER ALL", "DROP SCHEMA soma CASCADE",
          "CREATE TABLE soma.fake(x int)", "CREATE TABLE public.accounts(x int)",
          "CREATE TEMP TABLE accounts(balance bigint)", "SET ROLE soma_owner",
          "SET ROLE soma_bob", "SET SESSION AUTHORIZATION soma_bob",
          "SELECT soma.set_rights('soma_alice',true,true,true,true)",
          "SELECT soma.claim('A:alice:one')", "SELECT soma.settle('A:alice:one','bad',0)",
          "UPDATE soma.subjects SET tenant_id='B'"]
        denied=[]
        for statement in attacks:
            with connect() as con:
                with self.assertRaises(psycopg.Error) as exc:
                    con.execute(statement)
                denied.append({'sql':statement,'sqlstate':exc.exception.sqlstate})
        with connect() as con:
            con.execute('SET search_path=pg_temp,public')
            self.assertEqual(invoke(con,'soma.read_state',{})['total'],90)
        with connect('soma_migration') as con:
            roles=con.execute("SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname LIKE 'soma_%' ORDER BY rolname").fetchall()
            for role in roles:
                if role[0]!='soma_migration':
                    self.assertEqual(role[1:],(False,False,False,False))
            with self.assertRaises(psycopg.Error):
                con.execute("UPDATE soma.subjects SET tenant_id='B' WHERE db_role='soma_alice'")
        DETAILS['sql_denials']=denied;DETAILS['roles']=roles
        audit()

    def test_g04_admin_counterexample_outside_boundary(self):
        with connect('soma_migration') as con:
            con.execute("UPDATE soma.accounts SET balance=balance+1 WHERE tenant_id='A' AND account_id='allocation_a'")
        with self.assertRaises(AssertionError):
            audit()
        DETAILS['administrator_counterexample']='Trusted administrator changed account without summary; oracle detects mismatch. Outside defense, not attack blocked.'

    def test_g05_transaction_and_independent_oracle(self):
        reset(budget=1000)
        r=adjust();self.assertEqual(r['committed_version'],1)
        before=audit()
        self.assert_denied(adjust('over',20,1),'CAPACITY_EXCEEDED')
        self.assertEqual(before,audit())
        self.assert_denied(adjust('negative',-46,1),'CAPACITY_EXCEEDED')
        with connect() as con:
            invoke(con,'soma.adjust_allocation',req('rollback',-1,1));con.rollback()
        self.assertEqual(before,audit())
        self.assertEqual(call('soma.read_state',{})['held'],60)

    def test_g06_concurrent_version_capacity_and_budget(self):
        results=parallel([lambda:adjust('a',6),lambda:adjust('b',6)])
        self.assertEqual(sum(r.get('status')=='COMMITTED' for r in results),1)
        self.assertEqual(audit()['operations'],1)
        # The stale contender retries its denied key with the now-observed version.
        loser='a' if results[0].get('status')!='COMMITTED' else 'b'
        self.assert_denied(adjust(loser,6,1),'CAPACITY_EXCEEDED')
        reset(cap=1000)
        def reserve(op):
            result=adjust(op,1)
            if result.get('code')=='VERSION_CONFLICT':
                result=adjust(op,1,call('soma.read_state',{})['version'])
            return result
        results=parallel([lambda:reserve('a'),lambda:reserve('b')])
        self.assertEqual(sorted(r.get('code',r['status']) for r in results),['BUDGET_EXHAUSTED','COMMITTED'])
        DETAILS['budget_race']=dict(decisions=results,worker_started=False,
            state_after_decisions=call('soma.read_state',{}),c_max=60,budget_limit=100,
            retry_rule='Only VERSION_CONFLICT retries same denied key with current valid version; final refusal must be BUDGET_EXHAUSTED')
        self.assertEqual(call('soma.read_state',{})['held'],60);audit()

    def test_g06_eight_clients_no_lost_update(self):
        reset(cap=1000,budget=100000)
        conflicts=[]
        def client(index):
            for attempt in range(32):
                version=call('soma.read_state',{})['version']
                result=adjust('client_'+str(index),1,version)
                if result.get('status')=='COMMITTED':
                    return result
                self.assertEqual(result.get('code'),'VERSION_CONFLICT');conflicts.append(index)
            raise AssertionError('Bounded retries exhausted')
        started=time.perf_counter()
        results=parallel([lambda i=i:client(i) for i in range(8)])
        state=call('soma.read_state',{})
        self.assertEqual((state['total'],state['version']),(98,8));audit()
        DETAILS['eight_clients']=dict(clients=8,commits=len(results),conflicts=len(conflicts),elapsed_s=time.perf_counter()-started)

    def test_g07_concurrent_replay_typed_conflict_and_later_version(self):
        reset(cap=1000,budget=10000)
        results=parallel([lambda:adjust(),lambda:adjust()])
        self.assertEqual(sum(r['replayed'] for r in results),1)
        self.assertEqual(audit()['operations'],1)
        self.assert_denied(adjust(delta=4),'IDEMPOTENCY_CONFLICT')
        self.assert_denied(adjust(version=1),'IDEMPOTENCY_CONFLICT')
        self.assert_denied(adjust(account='allocation_b'),'IDEMPOTENCY_CONFLICT')
        adjust('later',1,1)
        r=adjust()
        self.assertEqual(r['committed_version'],1)
        self.assertEqual(r['feedback']['state_version'],2)
        self.assertTrue(r['replayed']);audit()

    def test_g07_status_during_uncommitted_operation(self):
        with connect() as con:
            invoke(con,'soma.adjust_allocation',req())
            pending=call('soma.operation_status',{'operation_id':'one'})
            self.assertEqual(pending['status'],'NOT_OBSERVED_OR_PENDING')
            con.commit()
        self.assertEqual(call('soma.operation_status',{'operation_id':'one'})['status'],'COMMITTED')
        self.assertTrue(adjust()['replayed']);audit()

    def test_g08_process_crashes_before_and_after_commit(self):
        records=[]
        for mode,expected_ops in [('before_commit',0),('after_commit',1)]:
            reset();marker=self.folder/(mode+'.marker')
            p=subprocess.Popen([sys.executable,str(HERE/'crash_client.py'),mode,'crash',str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                wait_marker(p,marker);barrier=json.loads(marker.read_text(encoding='utf-8'))
                termination=kill_attested_holder(p,barrier,verify_sql=True)
            finally:
                if p.poll() is None: p.kill();p.communicate(timeout=10)
            self.assertNotEqual(p.returncode,0)
            self.assertEqual(audit()['operations'],expected_ops)
            new=run_process('crash_client.py',['verify','crash','unused'])
            self.assertEqual(new.returncode,0,new.stderr)
            r=json.loads(new.stdout)
            self.assertEqual(r['replayed'],bool(expected_ops))
            self.assertEqual(audit()['operations'],1)
            self.assertNotEqual(r['verification_process_pid'],barrier['process_pid'])
            records.append(dict(boundary=mode,barrier=barrier,termination=termination,terminated_exit=p.returncode,new_process_result=r))
        DETAILS['crashes']=records

    def test_g08_transport_error_after_real_commit_is_pending(self):
        real_connect=connect
        class LostCommitReply:
            def __enter__(self):
                self.con=real_connect();return self.con.__enter__()
            def __exit__(self,*args):
                self.con.__exit__(*args)
                raise psycopg.OperationalError('Synthetic transport loss after actual commit')
        with mock.patch('db.connect',side_effect=lambda *a,**kw:LostCommitReply()):
            result=adjust()
        self.assertEqual(result,{'status':'OUTCOME_PENDING','code':'OUTCOME_PENDING'})
        self.assertEqual(project(result)[0]['next_action'],'RETRY_SAME_KEY_OR_QUERY')
        self.assertEqual(call('soma.operation_status',{'operation_id':'one'})['status'],'COMMITTED')
        self.assertTrue(adjust()['replayed'])
        self.assertEqual(audit()['operations'],1)
        DETAILS['commit_transport_injection']={'injected_after_actual_commit':True,'actual_network_failure':False,'returned':result}

    def test_g09_unknown_budget_lease_and_bad_ack(self):
        eid=adjust()['event_id'];transition('claim',eid)
        transition('mark_unknown',eid)
        state=call('soma.read_state',{})
        self.assertEqual((state['held'],state['unknown'],state['feedback']['budget_remaining']),(0,60,40))
        self.assert_denied(adjust('new',-1,1),'BUDGET_EXHAUSTED')
        self.assertEqual(transition('settle',eid,'bad',40)['status'],'RECONCILIATION_REQUIRED')
        with connect('soma_migration') as con:
            ph=con.execute('SELECT payload_hash FROM soma.outbox WHERE event_id=%s',(eid,)).fetchone()[0]
        self.assertEqual(transition('settle',eid,ph,61)['status'],'RECONCILIATION_REQUIRED')
        self.assertEqual(call('soma.read_state',{})['unknown'],60)
        audit()
        # A legitimate durable receiver receipt, never absence, authorizes settlement.
        item=transition('claim',eid)
        ack=receiver_call(self.receiver,item)
        self.assertEqual(transition('settle',eid,ack['payload_hash'],ack['cost'])['status'],'ACKED')
        self.assertTrue(transition('settle',eid,ack['payload_hash'],ack['cost'])['replayed'])
        self.assertEqual(call('soma.read_state',{})['spent'],40);audit()

    def test_g09_expired_lease_retry_limit_preserves_exposure(self):
        eid=adjust()['event_id']
        for i in range(3):
            self.assertEqual(transition('claim',eid)['status'],'CLAIMED')
            with connect('soma_migration') as con:
                con.execute("UPDATE soma.outbox SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(eid,))
        self.assertEqual(transition('claim',eid)['status'],'RECONCILE_ONLY')
        state=call('soma.read_state',{})
        self.assertEqual((state['held'],state['unknown']),(0,60))
        self.assertEqual(deliver(eid,self.receiver)['status'],'UNKNOWN')
        self.assertEqual(call('soma.read_state',{})['unknown'],60);audit()

    def test_g10_receiver_crash_worker_recovery_two_workers_late_ack(self):
        eid=adjust()['event_id'];marker=self.folder/'received.marker'
        p=subprocess.Popen([sys.executable,str(HERE/'worker.py'),eid,str(self.receiver),str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            wait_marker(p,marker);worker_barrier=json.loads(marker.read_text(encoding='utf-8'))
            worker_termination=kill_attested_holder(p,worker_barrier)
        finally:
            if p.poll() is None:p.kill();p.communicate(timeout=10)
        ack=receiver_call(self.receiver,{'event_id':eid,'query':True})
        self.assertEqual(ack['cost'],40)
        self.assertEqual(call('soma.read_state',{})['held'],60)
        with connect('soma_migration') as con:
            con.execute("UPDATE soma.outbox SET lease_until=clock_timestamp()-interval '1 second' WHERE event_id=%s",(eid,))
        results=parallel([lambda:deliver(eid,self.receiver),lambda:deliver(eid,self.receiver)])
        self.assertIn('ACKED',[r['status'] for r in results])
        self.assertTrue(transition('settle',eid,ack['payload_hash'],ack['cost'])['replayed'])
        # Deliberate retransmission proves receiver deduplicates both effect and charge.
        with connect('soma_migration') as con:
            body,ph=con.execute('SELECT payload,payload_hash FROM soma.outbox WHERE event_id=%s',(eid,)).fetchone()
        receiver_call(self.receiver,dict(event_id=eid,payload=body,payload_hash=ph))
        received=receiver_call(self.receiver,{'event_id':eid,'query':True})
        self.assertEqual((received['cost'],received['attempts']),(40,2))
        state=call('soma.read_state',{})
        self.assertEqual((state['spent'],state['held'],state['unknown']),(40,0,0))
        DETAILS['receiver_crash_recovery']=dict(barrier=worker_barrier,termination=worker_termination,terminated_exit=p.returncode,workers=results,receiver=received)
        audit()

    def test_g11_official_sdk_full_stdio_chain(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def scenario():
            params=StdioServerParameters(command=sys.executable,args=[str(HERE/'server.py')],env=dict(os.environ,SOMA_ROLE='soma_alice'))
            with (self.folder/'sdk_server.log').open('w',encoding='utf-8') as log:
                async with stdio_client(params,errlog=log) as (read,write):
                    async with ClientSession(read,write) as session:
                        init=await session.initialize()
                        tools=await session.list_tools()
                        self.assertEqual({t.name for t in tools.tools},{'soma.read_state','soma.adjust_allocation','soma.operation_status'})
                        import jsonschema
                        for tool in tools.tools:jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                        outputs=[]
                        for tool,args in [('soma.read_state',{}),('soma.adjust_allocation',req()),('soma.adjust_allocation',req()),
                                          ('soma.adjust_allocation',req('over',20,1)),('soma.operation_status',{'operation_id':'one'}),
                                          ('soma.read_state',{}),('soma.adjust_allocation',req('bad',1.0,1)),
                                          ('soma.adjust_allocation',dict(req('bad2',1,1),tenant='B'))]:
                            result=await session.call_tool(tool,args)
                            outputs.append(result.model_dump(by_alias=True,exclude_none=True))
                            self.assertLessEqual(len(result.content[0].text.encode()),1024)
                        self.assertFalse(outputs[1]['isError']);self.assertTrue(outputs[2]['structuredContent']['replayed'])
                        self.assertEqual(outputs[3]['structuredContent']['code'],'CAPACITY_EXCEEDED')
                        self.assertTrue(outputs[-1]['isError']);self.assertTrue(outputs[-2]['isError'])
                        return dict(protocol=init.protocolVersion,server=init.serverInfo.model_dump(),tools=[t.model_dump(by_alias=True) for t in tools.tools],outputs=outputs)
        try:
            DETAILS['sdk']=asyncio.run(asyncio.wait_for(scenario(),timeout=30))
        except BaseException:
            if (self.folder/'sdk_server.log').exists():
                print((self.folder/'sdk_server.log').read_text(encoding='utf-8'),file=sys.stderr)
            raise
        audit()

    def test_g12_bounded_projection_all_catalogue_paths(self):
        outputs=[call('soma.read_state',{}),call('soma.operation_status',{'operation_id':'missing'}),adjust(),
                 adjust(),adjust('over',20,1),call('soma.adjust_allocation',dict(req(),claimed_safe=True))]
        eid=outputs[2]['event_id'];transition('mark_unknown',eid)
        outputs.append(call('soma.operation_status',{'operation_id':'one'}))
        outputs.append(call('soma.read_state',{}))
        for result in outputs:
            public,text=project(result)
            self.assertLessEqual(len(text.encode()),1024)
            self.assertNotIn('feedback',public)
        status=outputs[-2]
        self.assertEqual(status['receipt']['delivery_status_at_commit'],'READY')
        self.assertEqual(status['delivery_state'],'UNKNOWN')
        DETAILS['projection_bytes']=[len(project(r)[1].encode()) for r in outputs]

    def test_g12_status_snapshot_across_uncommitted_settlement(self):
        eid=adjust()['event_id'];item=transition('claim',eid)
        ack=receiver_call(self.receiver,item)
        with connect('soma_worker') as worker:
            worker.execute('SELECT soma.settle(%s,%s,%s)',(eid,ack['payload_hash'],ack['cost']))
            before=call('soma.operation_status',{'operation_id':'one'})
            self.assertEqual((before['delivery_state'],before['reservation_state']),('IN_FLIGHT','HELD'))
            self.assertEqual(before['feedback']['budget_remaining'],40)
            worker.commit()
        after=call('soma.operation_status',{'operation_id':'one'})
        self.assertEqual((after['delivery_state'],after['reservation_state']),('ACKED','SETTLED'))
        self.assertEqual(after['feedback']['budget_remaining'],60)
        self.assertEqual(before['feedback']['state_version'],after['feedback']['state_version'])
        self.assertEqual(after['feedback']['state_version_scope'],'BUSINESS_MUTATIONS_ONLY')
        DETAILS['status_settlement_snapshots']=dict(before_commit=before,after_commit=after)
        # Incompatible ACK after terminal settlement is reported without reopening exposure.
        self.assertEqual(transition('claim',eid)['status'],'ACKED')
        self.assertEqual(transition('settle',eid,'incompatible',41)['status'],'RECONCILIATION_REQUIRED')
        self.assertEqual(call('soma.read_state',{})['spent'],40)
        self.assertEqual(call('soma.read_state',{})['unknown'],0);audit()

    def test_g12_frame_limit_precedes_json_parser(self):
        p=run_process('server.py',[],input='x'*16385+'\n')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('MCP_FRAME_LIMIT',p.stderr)
        self.assertEqual(audit()['operations'],0)

    def test_g16_server_and_actual_roles(self):
        with connect('soma_migration') as con:
            version=con.execute('SELECT version()').fetchone()[0]
            settings=dict(con.execute("SELECT name,setting FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes','listen_addresses','max_connections')").fetchall())
        self.assertIn('PostgreSQL 18.7',version)
        self.assertEqual(settings['fsync'],'on');self.assertEqual(settings['synchronous_commit'],'on')
        self.assertEqual(settings['listen_addresses'],'127.0.0.1')
        identities={}
        for role in ('soma_alice','soma_peer','soma_bob','soma_worker'):
            with connect(role) as con:identities[role]=con.execute('SELECT session_user,current_user').fetchone()
            self.assertEqual(identities[role],(role,role))
        DETAILS['postgres']=dict(version=version,settings=settings,authenticated_roles=identities)


def run():
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Integration)
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    return dict(status='PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
                tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),log=stream.getvalue(),details=DETAILS)


if __name__=='__main__':
    report=run();(ROOT/'results/integration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(report['log']);sys.exit(0 if report['status']=='PASS' else 1)
