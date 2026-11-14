"""Predeclared deterministic quota controller over actual official MCP sessions.

No model calls. The conventional controller propagates known own effects rather
than being weakened with forced state reads. The enriched control has the same
information as SOMA. All external events follow logical steps, never RPC counts.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

from bridge_server import ARMS,SOMA_KEY,CLASSIC_KEY
from db import call,connect
from fixtures import reset
from integrity_oracle import audit
from worker import transition,deliver
from task_oracle import FIXTURES,check as task_check

ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/'implementation'
DELTAS=FIXTURES['proposed_deltas']
SCENARIOS=('steady','peer_changes','replay_after_peer','unknown_budget','revocation')


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)


@asynccontextmanager
async def sdk_session(arm,*,script='bridge_server.py',extra_env=None,log_path=None):
    env=dict(os.environ,SOMA_ROLE='soma_alice',SOMA_FACADE=arm)
    if extra_env:env.update(extra_env)
    params=StdioServerParameters(command=os.sys.executable,args=[str(HERE/script)],env=env)
    log_path=log_path or ROOT/'.runtime'/f'sdk_{uuid.uuid4().hex}.log'
    log_path.parent.mkdir(exist_ok=True,parents=True)
    with log_path.open('w',encoding='utf-8') as log:
        async with stdio_client(params,errlog=log) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=10)) as session:
                init=await session.initialize()
                tools=await session.list_tools()
                assert {t.name for t in tools.tools}=={'soma.read_state','soma.adjust_allocation','soma.operation_status'}
                yield session,init


class Controller:
    def __init__(self,session,arm):
        self.session,self.arm=session,arm
        self.cache=None
        self.balance=40
        self.requests=[]
        self.history={}
        self.outcomes=[]
        self.pending=None
        self.max_projection_bytes=0

    async def rpc(self,tool,args,step,reason):
        cache_before=None if self.cache is None else self.cache.copy()
        start=time.perf_counter()
        result=await self.session.call_tool(tool,args)
        elapsed=time.perf_counter()-start
        data=result.model_dump(by_alias=True,exclude_none=True)
        content=result.structuredContent or {}
        meta=data.get('_meta',{})
        feedback=meta.get(SOMA_KEY,meta.get(CLASSIC_KEY))
        projection=sum(len(x.text.encode('utf-8')) for x in result.content if x.type=='text')
        assert projection<=1024
        self.max_projection_bytes=max(self.max_projection_bytes,projection)
        self.requests.append(dict(step=step,transition_reason=reason,tool=tool,arguments=args.copy(),result=data,cache_before=cache_before,
            elapsed_s=elapsed,normalized_response_bytes=len(canonical(data).encode()),projection_text_bytes=projection))
        if feedback is not None:
            self.cache={k:feedback[k] for k in ('state_version','capacity_remaining','budget_remaining')}
        return content,feedback

    async def refresh(self,step,reason='INITIAL_OBSERVATION'):
        result,feedback=await self.rpc('soma.read_state',{},step,reason)
        if result.get('status')!='OBSERVED':return result.get('code','NO_OBSERVATION')
        self.cache=dict(state_version=result['version'],capacity_remaining=result['capacity']-result['total'],
            budget_remaining=result['budget_limit']-result['spent']-result['held']-result['unknown'])
        return None

    async def replay(self,key,step):
        request=self.history[key]['request']
        result,feedback=await self.rpc('soma.adjust_allocation',request,step,'HISTORICAL_EXACT_REPLAY')
        assert result.get('status')=='COMMITTED' and result['replayed']
        # A historical receipt does NOT overwrite a newer inferred state. The
        # minimal arm keeps its optimistic cache and can encounter VERSION_CONFLICT.
        return result

    async def resolve_pending(self):
        pending=self.pending
        request,step=pending['request'],pending['step']
        known,feedback=await self.rpc('soma.operation_status',{'operation_id':request['operation_id']},step,'UNCERTAIN_STATUS_SAME_KEY')
        if known.get('status')!='COMMITTED':
            return dict(step=step,status='OUTCOME_PENDING',delta=0)
        receipt=known['receipt']
        if step not in self.history:
            self.balance+=request['delta']
            self.history[step]=dict(request=request,result=receipt)
        self.pending=None
        if feedback is None:self.cache=None
        return dict(step=step,status='COMMITTED',delta=request['delta'],version=receipt['committed_version'])

    async def execute_step(self,step,requested):
        if self.pending is not None:
            pending_step=self.pending['step']
            recovered=await self.resolve_pending()
            if self.pending is not None:return dict(step=step,status='OUTCOME_PENDING',delta=0)
            if step==pending_step:return recovered
        capacity_refreshed=False
        if self.cache is None:
            error=await self.refresh(step)
            if error:return dict(step=step,status=error,delta=0)
        for attempt in range(4):
            if self.cache['budget_remaining']<60:
                error=await self.refresh(step,'INSUFFICIENT_CACHED_BUDGET')
                if error:return dict(step=step,status=error,delta=0)
                if self.cache['budget_remaining']<60:
                    return dict(step=step,status='SKIPPED_BUDGET',delta=0)
            delta=min(requested,self.cache['capacity_remaining']) if requested>0 else max(requested,-self.balance)
            if delta==0 and not capacity_refreshed:
                error=await self.refresh(step,'ZERO_FROM_CACHED_CAPACITY')
                capacity_refreshed=True
                if error:return dict(step=step,status=error,delta=0)
                delta=min(requested,self.cache['capacity_remaining']) if requested>0 else max(requested,-self.balance)
            if delta==0:return dict(step=step,status='SKIPPED_ZERO',delta=0)
            request=dict(operation_id=f'task_{step}',account_id='allocation_a',delta=delta,
                         expected_version=self.cache['state_version'])
            previous=self.cache.copy()
            # Write ahead in client memory BEFORE dispatch. An SDK exception leaves
            # this exact request pending for host-driven session recovery.
            self.pending=dict(step=step,request=request.copy())
            result,feedback=await self.rpc('soma.adjust_allocation',request,step,'NEW_MUTATION_ATTEMPT')
            if result.get('status')!='OUTCOME_PENDING':self.pending=None
            if result.get('status')=='COMMITTED':
                assert not result['replayed']
                # With read_receipt revoked the permitted ACK is intentionally
                # minimal. A confirmed new admission implies ev+1 by the kernel
                # contract; deriving that OWN version grants no new state access.
                committed_version=result.get('committed_version',request['expected_version']+1)
                self.balance+=delta
                self.history[step]=dict(request=request,result=result)
                if feedback is None:
                    # Conservative exact own-effect propagation, not forced I/O.
                    self.cache=dict(state_version=committed_version,
                        capacity_remaining=previous['capacity_remaining']-delta,
                        budget_remaining=previous['budget_remaining']-60)
                return dict(step=step,status='COMMITTED',delta=delta,version=committed_version)
            if result.get('code')=='VERSION_CONFLICT':
                error=await self.refresh(step,'VERSION_CONFLICT_REFRESH')
                if error:return dict(step=step,status=error,delta=0)
                continue
            if result.get('code')=='BUDGET_EXHAUSTED':
                error=await self.refresh(step,'CONFIRMED_BUDGET_REFUSAL')
                if error:return dict(step=step,status=error,delta=0)
                return dict(step=step,status='BUDGET_EXHAUSTED',delta=0)
            if result.get('status')=='OUTCOME_PENDING':
                # Do not change arguments or key after uncertainty.
                return await self.resolve_pending()
            return dict(step=step,status=result.get('code',result.get('status','UNKNOWN')),delta=0)
        raise AssertionError('Bounded controller version retries exhausted')


class World:
    def __init__(self,scenario,receiver):
        self.scenario,self.receiver=scenario,receiver
        self.events=[]

    def peer(self,step,event):
        state=call('soma.read_state',{},'soma_peer')
        request=dict(operation_id=f'peer_{step}',account_id=event['account'],delta=event['delta'],expected_version=state['version'])
        result=call('soma.adjust_allocation',request,'soma_peer')
        assert result.get('status')=='COMMITTED'
        self.events.append(dict(step=step,event='PEER_MUTATION',request=request,result=result))

    def before(self,step,known_event_ids):
        replay=None
        for event in FIXTURES['scenarios'][self.scenario]['events']:
            if event['at']!=step:continue
            if event['kind']=='peer':self.peer(step,event)
            elif event['kind']=='replay':
                replay=event['task']
                self.events.append(dict(step=step,event='REPLAY_REQUESTED',task=replay))
            elif event['kind']=='unknown':
                key=event['task']
                eid=known_event_ids[key]
                result=transition('mark_unknown',eid)
                self.events.append(dict(step=step,event='UNKNOWN',event_id=eid,result=result))
            elif event['kind']=='settle':
                key=event['task']
                eid=known_event_ids[key]
                result=deliver(eid,self.receiver)
                assert result['status']=='ACKED'
                self.events.append(dict(step=step,event='SETTLED',event_id=eid,result=result))
            elif event['kind']=='enabled':
                enabled=event['value']
                with connect('soma_migration') as con:
                    con.execute("SELECT soma.set_rights('soma_alice',%s,true,true,true)",(enabled,))
                self.events.append(dict(step=step,event='ENABLED',value=enabled))
            else:raise AssertionError('Unrecognized frozen intervention')
        return replay


def normalize_request(record):
    data=json.loads(canonical(record['result']))
    meta=data.get('_meta',{})
    if SOMA_KEY in meta:meta['CURRENT_OBSERVATION']=meta.pop(SOMA_KEY)
    if CLASSIC_KEY in meta:meta['CURRENT_OBSERVATION']=meta.pop(CLASSIC_KEY)
    return dict(step=record['step'],transition_reason=record['transition_reason'],tool=record['tool'],arguments=record['arguments'],result=data)


async def one(scenario,arm,repetition):
    started=time.perf_counter()
    fixture=reset(budget=FIXTURES['scenarios'][scenario]['budget'])
    initialization_s=time.perf_counter()-started
    world=World(scenario,ROOT/'.runtime/receivers'/f'{uuid.uuid4().hex}.sqlite')
    world.receiver.parent.mkdir(parents=True,exist_ok=True)
    audits=[];task_audits=[];world_s=0.;audit_s=0.;controller_s=0.
    setup=time.perf_counter()
    async with sdk_session(arm) as (session,init):
        setup_s=time.perf_counter()-setup
        controller=Controller(session,arm)
        assert await controller.refresh(-1) is None
        for step,requested in enumerate(DELTAS):
            before=time.perf_counter()
            event=world.before(step,{k:v['result']['event_id'] for k,v in controller.history.items()})
            world_s+=time.perf_counter()-before
            before=time.perf_counter()
            if event is not None:await controller.replay(event,step)
            outcome=await controller.execute_step(step,requested)
            controller.outcomes.append(outcome)
            controller_s+=time.perf_counter()-before
            before=time.perf_counter();audits.append(audit());task_audits.append(task_check(scenario,step,outcome));audit_s+=time.perf_counter()-before
    elapsed=time.perf_counter()-started
    assert elapsed<=600 and len(controller.requests)<2000
    trace=dict(deltas=DELTAS,scenario=scenario,recipe=FIXTURES['scenarios'][scenario])
    return dict(scenario=scenario,arm=arm,repetition=repetition,fixture=fixture,
        trace_sha256=hashlib.sha256(canonical(trace).encode()).hexdigest(),protocol=init.protocolVersion,
        server=init.serverInfo.model_dump(),outcomes=controller.outcomes,world_events=world.events,
        checkpoints=audits,task_checkpoints=task_audits,final_state=audits[-1],requests=controller.requests,
        rpc_count=len(controller.requests),read_state_count=sum(r['tool']=='soma.read_state' for r in controller.requests),
        mutation_calls=sum(r['tool']=='soma.adjust_allocation' for r in controller.requests),
        version_conflicts=sum((r['result'].get('structuredContent') or {}).get('code')=='VERSION_CONFLICT' for r in controller.requests),
        normalized_response_bytes=sum(r['normalized_response_bytes'] for r in controller.requests),
        projection_text_bytes=sum(r['projection_text_bytes'] for r in controller.requests),
        max_projection_bytes=controller.max_projection_bytes,
        timing_s=dict(initialization=initialization_s,sdk_setup=setup_s,controller=controller_s,world=world_s,audit=audit_s,total=elapsed))


async def run():
    runs=[];comparisons=[]
    for scenario in SCENARIOS:
        for repetition in range(3):
            # Rotate execution order; no statistical independence is assumed.
            order=ARMS[repetition:]+ARMS[:repetition]
            cases=[await one(scenario,arm,repetition) for arm in order]
            byarm={c['arm']:c for c in cases}
            first=byarm['conventional']
            for case in cases:
                assert case['trace_sha256']==first['trace_sha256']
                assert case['outcomes']==first['outcomes']
                assert case['final_state']==first['final_state']
                assert case['checkpoints']==first['checkpoints']
                assert case['task_checkpoints']==first['task_checkpoints']
                assert case['world_events']==first['world_events']
            b,c=byarm['soma'],byarm['conventional_enriched']
            assert [normalize_request(r) for r in b['requests']]==[normalize_request(r) for r in c['requests']]
            comparisons.append(dict(scenario=scenario,repetition=repetition,status='PASS',
                same_business_outcomes=True,same_final_state=True,same_world_events=True,
                enriched_information_equivalent=True,
                calls={arm:byarm[arm]['rpc_count'] for arm in ARMS},
                normalized_bytes={arm:byarm[arm]['normalized_response_bytes'] for arm in ARMS}))
            runs.extend(cases)
    return dict(status='PASS',sessions=len(runs),comparisons=comparisons,runs=runs,
        controller='Deterministic optimized exact own-effect propagation; no LLM',
        measurement='Normalized reconstructed SDK response JSON bytes, not raw stdio bytes or tokens',
        limits=['Five hand-designed finite scenarios, three timing repetitions; no population/statistical claim.',
                'Conventional enriched and SOMA are information-equivalent by construction.',
                'Host/SQL/receiver trusted; no production qualification or client pilot.',
                'External events follow logical steps and are common to all arms.'])


if __name__=='__main__':
    result=asyncio.run(run())
    (ROOT/'results/pilot.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],sessions=result['sessions'],comparisons=len(result['comparisons']))))
