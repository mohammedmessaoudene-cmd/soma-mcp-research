"""Risk-directed bridge and real SDK interruption regression tests."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from bridge_server import ARMS,build_result,SOMA_KEY,CLASSIC_KEY
from db import call,project
from fixtures import reset
from integrity_oracle import audit
from db import connect
from pilot import sdk_session,Controller

DETAILS={}


def request():
    return dict(operation_id='r4_crash',account_id='allocation_a',delta=2,expected_version=0)


def leaves(exc):
    if hasattr(exc,'exceptions'):
        return [item for child in exc.exceptions for item in leaves(child)]
    return [exc]


class R4Tests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_information_equivalent_bridge(self):
        result=call('soma.adjust_allocation',request())
        a,b,c=[build_result(result,arm) for arm in ARMS]
        self.assertEqual(a[:2],b[:2]);self.assertEqual(b[:2],c[:2])
        self.assertEqual(a[2],{})
        self.assertEqual(b[2][SOMA_KEY],c[2][CLASSIC_KEY])
        self.assertLessEqual(len(b[1].encode()),1024)
        audit()

    def test_sdk_bridge_permissions_all_arms(self):
        async def scenario():
            cases=[]
            for arm in ARMS:
                reset()
                with connect('soma_migration') as con:
                    con.execute("SELECT soma.set_rights('soma_alice',true,false,true,true)")
                async with sdk_session(arm) as (session,init):
                    result=await session.call_tool('soma.adjust_allocation',request())
                    data=result.model_dump(by_alias=True,exclude_none=True)
                    self.assertFalse(result.isError);self.assertEqual(data.get('_meta',{}),{})
                    with connect('soma_migration') as con:
                        con.execute("SELECT soma.set_rights('soma_alice',true,false,true,false)")
                    replay=await session.call_tool('soma.adjust_allocation',request())
                    self.assertTrue(replay.isError)
                    self.assertEqual(replay.structuredContent['code'],'NOT_AUTHORIZED')
                    self.assertEqual(replay.model_dump(by_alias=True).get('_meta',{}),{})
                    bad=await session.call_tool('soma.adjust_allocation',dict(request(),claimed_safe=True))
                    self.assertTrue(bad.isError);self.assertEqual(bad.structuredContent['code'],'INVALID_ARGUMENT')
                    cases.append(dict(arm=arm,protocol=init.protocolVersion,authorized_without_feedback=data,
                        forbidden_replay=replay.model_dump(by_alias=True,exclude_none=True)))
                self.assertEqual(audit()['operations'],1)
            return cases
        DETAILS['sdk_permissions']=asyncio.run(scenario())

    def test_stale_zero_capacity_refreshes_before_abandon(self):
        reset(budget=1000)
        async def scenario():
            async with sdk_session('conventional') as (session,init):
                controller=Controller(session,'conventional')
                first=await controller.execute_step(0,10)
                self.assertEqual(first['delta'],10)
                peer=call('soma.adjust_allocation',dict(operation_id='free',account_id='allocation_b',delta=-5,expected_version=1),'soma_peer')
                self.assertEqual(peer['status'],'COMMITTED')
                second=await controller.execute_step(1,4)
                self.assertEqual(second['status'],'COMMITTED');self.assertEqual(second['delta'],4)
                self.assertIn('ZERO_FROM_CACHED_CAPACITY',[r['transition_reason'] for r in controller.requests])
                return dict(second=second,oracle=audit())
        DETAILS['stale_zero_capacity']=asyncio.run(scenario())

    def test_controller_handles_redacted_new_receipt(self):
        async def scenario():
            cases=[]
            for arm in ARMS:
                reset(budget=1000)
                with connect('soma_migration') as con:
                    con.execute("SELECT soma.set_rights('soma_alice',true,true,true,false)")
                async with sdk_session(arm) as (session,init):
                    controller=Controller(session,arm)
                    first=await controller.execute_step(0,4)
                    second=await controller.execute_step(1,4)
                    self.assertEqual((first['version'],second['version']),(1,2))
                    self.assertIsNone(controller.pending)
                    for item in controller.requests:
                        if item['tool']=='soma.adjust_allocation':
                            content=item['result']['structuredContent']
                            self.assertNotIn('committed_version',content)
                            self.assertNotIn('event_id',content)
                            self.assertNotIn('admitted_policy_version',content)
                    cases.append(dict(arm=arm,first=first,second=second,oracle=audit()))
            return cases
        DETAILS['redacted_receipt_controller']=asyncio.run(scenario())

    def test_oracle_detects_trusted_owner_receipt_and_payload_corruption(self):
        call('soma.adjust_allocation',request());audit()
        with connect('soma_migration') as con:
            con.execute("UPDATE soma.operations SET result_payload=jsonb_set(result_payload,'{committed_version}','999')")
        with self.assertRaises(AssertionError):audit()
        reset();call('soma.adjust_allocation',request())
        with connect('soma_migration') as con:
            con.execute("UPDATE soma.outbox SET payload='tampered',payload_hash='not-a-hash'")
        with self.assertRaises((AssertionError,json.JSONDecodeError)):audit()
        DETAILS['oracle_corruption']='Privileged corruption detected; trusted owner remains outside threat defense'

    def test_oracle_preserves_json_boolean_integer_distinction(self):
        for replacement in (True,1.0):
            reset();call('soma.adjust_allocation',dict(request(),delta=1));audit()
            with connect('soma_migration') as con:
                con.execute("UPDATE soma.operations SET result_payload=jsonb_set(result_payload,'{committed_version}',%s::jsonb)",(json.dumps(replacement),))
            with self.assertRaises(AssertionError):audit()
            reset();call('soma.adjust_allocation',dict(request(),delta=1))
            with connect('soma_migration') as con:
                body=json.loads(con.execute('SELECT payload FROM soma.outbox').fetchone()[0])
                body['delta']=replacement
                text=json.dumps(body)
                con.execute('UPDATE soma.outbox SET payload=%s,payload_hash=%s',(text,hashlib.sha256(text.encode()).hexdigest()))
            with self.assertRaises(AssertionError):audit()
        DETAILS['json_type_integrity']='true and 1.0 are rejected in place of integer1 even with matching altered payload SHA'

    def test_pending_request_retained_until_resolved(self):
        import mcp.types as types
        class FakeSession:
            def __init__(self):self.calls=[];self.committed=False
            async def call_tool(self,tool,args):
                self.calls.append((tool,args.copy()))
                if tool=='soma.adjust_allocation':result=dict(status='OUTCOME_PENDING',code='OUTCOME_PENDING')
                elif self.committed:result=dict(status='COMMITTED',receipt=dict(status='COMMITTED',operation_id='task_0',committed_version=1,event_id='A:alice:task_0'))
                else:result=dict(status='NOT_OBSERVED_OR_PENDING')
                return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result))],structuredContent=result)
        async def scenario():
            session=FakeSession();controller=Controller(session,'conventional')
            controller.cache=dict(state_version=0,capacity_remaining=10,budget_remaining=100)
            self.assertEqual((await controller.execute_step(0,2))['status'],'OUTCOME_PENDING')
            saved=controller.pending['request'].copy()
            self.assertEqual((await controller.execute_step(1,8))['status'],'OUTCOME_PENDING')
            self.assertEqual(controller.pending['request'],saved)
            self.assertEqual(sum(t=='soma.adjust_allocation' for t,a in session.calls),1)
            session.committed=True
            self.assertEqual((await controller.execute_step(0,2))['status'],'COMMITTED')
            self.assertEqual(controller.balance,42);self.assertIsNone(controller.pending)
            return dict(scope='Deterministic unit fault fixture, not the separate actual SDK process-termination proof',calls=session.calls,saved_request=saved)
        DETAILS['pending_request']=asyncio.run(scenario())

    def test_actual_sdk_server_interruption_after_commit(self):
        with tempfile.TemporaryDirectory(prefix='soma_r4_fault_') as folder:
            marker=Path(folder)/'committed.json'
            async def scenario():
                state=dict(killed=False,transport_failure=None,context_exit=None)
                try:
                    async with sdk_session('soma',script='fault_server.py',extra_env={'SOMA_R4_FAILPOINT_MARKER':str(marker)}) as (session,init):
                        pending=asyncio.create_task(session.call_tool('soma.adjust_allocation',request()))
                        deadline=time.monotonic()+10
                        while not marker.exists() and time.monotonic()<deadline:
                            if pending.done():await pending
                            await asyncio.sleep(.01)
                        self.assertTrue(marker.exists(),'Server did not reach actual commit barrier')
                        barrier=json.loads(marker.read_text(encoding='utf-8'))
                        self.assertEqual(barrier['operation_id'],request()['operation_id'])
                        known=call('soma.operation_status',{'operation_id':request()['operation_id']})
                        self.assertEqual(known['status'],'COMMITTED')
                        self.assertEqual(audit()['operations'],1)
                        self.assertNotEqual(barrier['process_pid'],os.getpid())
                        os.kill(barrier['process_pid'],signal.SIGTERM);state['killed']=True
                        state['barrier']=barrier
                        state['protocol']=init.protocolVersion
                        try:
                            await asyncio.wait_for(pending,timeout=5)
                            self.fail('SDK unexpectedly received a reply after process interruption')
                        except AssertionError:raise
                        except Exception as exc:
                            state['transport_failure']=type(exc).__name__
                except Exception as exc:
                    if any(isinstance(item,AssertionError) for item in leaves(exc)):raise
                    if not state['killed']:raise
                    state['context_exit']=type(exc).__name__
                self.assertTrue(state['killed']);self.assertIsNotNone(state['transport_failure'])
                fresh_marker=Path(folder)/'restarted_server.json'
                command=[sys.executable,str(Path(__file__).resolve().parent/'recovery_client.py'),json.dumps(request()),str(fresh_marker)]
                new=await asyncio.to_thread(subprocess.run,command,capture_output=True,text=True,timeout=20)
                self.assertEqual(new.returncode,0,new.stderr)
                recovered=json.loads(new.stdout)
                self.assertEqual(recovered['request'],request())
                self.assertEqual(recovered['status']['status'],'COMMITTED')
                self.assertTrue(recovered['replay']['replayed'])
                self.assertNotEqual(recovered['server_process_pid'],state['barrier']['process_pid'])
                self.assertNotEqual(recovered['client_process_pid'],os.getpid())
                state['initial_client_process_pid']=os.getpid()
                state['recovery']=recovered
                state['oracle']=audit();self.assertEqual(state['oracle']['operations'],1)
                state['scope']='Actual attested MCP server process termination, not simulated SDK exception, network outage or power failure'
                return state
            DETAILS['actual_sdk_interruption']=asyncio.run(scenario())


def run():
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(R4Tests))
    return dict(status='PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
                tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),log=stream.getvalue(),details=DETAILS)


if __name__=='__main__':
    print(json.dumps(run(),indent=2))
