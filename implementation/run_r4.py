"""Fresh PostgreSQL + core regressions + bridge tests + frozen SDK pilot.

Public outputs omit absolute host paths. Private raw exceptions remain in the
excluded .runtime tree. Candidate/publication reviews are separate real actions.
"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import sys
import time
import traceback

from identity_r4 import ROOT,identities,sha
from verify_dependencies import verify
from provision import start,stop
import test_integration
import test_r4
import pilot


def write(name,data):
    (ROOT/'results').mkdir(exist_ok=True)
    (ROOT/'results'/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def summarize(report):
    rows=[]
    for scenario in pilot.SCENARIOS:
        for arm in pilot.ARMS:
            cases=[r for r in report['runs'] if r['scenario']==scenario and r['arm']==arm]
            row=dict(scenario=scenario,arm=arm,repetitions=len(cases),
                rpc_counts=[r['rpc_count'] for r in cases],read_counts=[r['read_state_count'] for r in cases],
                conflicts=[r['version_conflicts'] for r in cases],normalized_bytes=[r['normalized_response_bytes'] for r in cases],
                controller_seconds=[r['timing_s']['controller'] for r in cases],
                final_tenant_a=cases[0]['final_state']['tenants'][0])
            rows.append(row)
    return dict(status='PASS',sessions=report['sessions'],comparison_groups=len(report['comparisons']),rows=rows,
        conclusion='SOMA and information-equivalent conventional enriched responses have identical normalized logical behavior. Additional current observations may affect RPC counts relative to minimal receipts; this is not an exclusive algorithmic advantage.',
        units='Reconstructed normalized SDK response JSON bytes; no raw-network, token or monetary claim',
        external_customer_pilot=False,llm_invocations=0,production_qualified=False)


def reports(summary,core,bridge,ids):
    table='| Scenario | Arm | RPC/session (3 repetitions) | Read-state/session | Normalized response bytes/session |\n|---|---|---|---|---|\n'
    for row in summary['rows']:
        table+='| '+row['scenario']+' | '+row['arm']+' | '+str(row['rpc_counts'])+' | '+str(row['read_counts'])+' | '+str(row['normalized_bytes'])+' |\n'
    common=f"\nCode ID: `{ids['code_id']}`\n\nDesign ID: `{ids['design_id']}`\n\n"+table
    english='''# SOMA R4: controlled MCP quota pilot

Research preview, 14 November 2026. This is a local deterministic-controller
experiment, not a customer pilot, production qualification or peer-reviewed paper.

The three arms share PostgreSQL, exact admission, rights, idempotency, reservations
and outbox. The minimal conventional client propagates its known own effects;
it is not forced to read after each mutation. The enriched conventional arm
receives the same permitted observations as SOMA under a different metadata key.
The five frozen recipes include 60 literal checkpoints declared before final
measurements. All 45 SDK sessions matched those task expectations and the full
integrity oracle; SOMA/enriched requests and responses matched after namespace
normalization. Repetitions are not independent tasks or statistical evidence.

'''+f'Core regression tests: {core["tests"]}; R4 tests: {bridge["tests"]}; skipped: 0.\n'
    english+='''The R4 oracle also checks receipt fields, exact JSON types, outbox payload/hash
and reconstruction of balances from operation effects. Privileged corruption tests
exercise this audit; the trusted database owner is not an adversary defended against.
An actual SDK server process was terminated after PostgreSQL commit before reply.
A fresh OS client/server replayed the same request and observed exactly one effect.
No electrical failure, physical disk durability or actual network outage was tested.

RPC counts include every intermediate refusal and replay; initialization/tools-list,
fixture setup, administrative interventions, receiver and audit time are separate.
Normalized bytes reconstruct the SDK result using a declared canonical JSON
serialization. They are not raw stdio bytes or model tokens. No LLM was invoked.

Identical-information conventional output matches SOMA; a difference against
minimal receipts is a consequence of exporting current observations for these
fixtures and policy, not proof of a unique SOMA algorithm. Full results are in
results/pilot.json. The code retains R3's trusted host/SQL/worker/receiver boundary.
The original R3 archive and private chat reviews are not dependencies of this
public reproducer. License choice is undeclared; this is not called open source.

Remaining external work: one real workflow owner, end-to-end identity and write
mediation in that service, actual receiver ACK/cost contract, integration effort
and human/LLM utility evaluation, then independent operational qualification.
None of those external outcomes is claimed by publishing this research preview.
'''+common
    french='''# SOMA R4 - bilan du pilote controle

Preversion de recherche du 14 novembre 2026. Ce pilote est LOCAL, avec un
controleur deterministe et des quotas synthetiques. Aucun client industriel,
revenu, avantage universel, nouveaute mondiale ou qualification de production
n'est etabli. Les hypotheses et recettes sont figees dans spec/.

Les trois bras partagent le noyau PostgreSQL et les garanties. Le conventionnel
minimal propage exactement ses propres effets; le conventionnel enrichi recoit
les memes observations autorisees que SOMA. Les 45 sessions SDK correspondent
aux attentes litterales des cinq recettes et aux oracles. Les decisions et
observations normalisees SOMA/conventionnel enrichi sont equivalentes.

'''+f'Tests noyau : {core["tests"]}. Tests R4 : {bridge["tests"]}. Aucun test ignore.\n'
    french+='''La reprise supplementaire tue le vrai serveur MCP apres commit avant reponse,
puis utilise un nouveau client et serveur pour rejouer la meme requete: un effet.
Ce n'est pas un test de coupure electrique. Les octets sont ceux des reponses SDK
reserialisees, pas les octets bruts ni des tokens. Aucun appel LLM n'est execute.

Une difference d'appels face aux recus minimaux mesure la disponibilite des
observations pour cette politique, pas un avantage algorithmique propre a SOMA.
La valeur de l'integration chez un client et l'utilite pour un agent LLM restent
a mesurer. Voir la note anglaise pour les hypotheses, droits et limites.
'''+common
    (ROOT/'docs').mkdir(exist_ok=True)
    (ROOT/'docs/TECHNICAL_NOTE_EN.md').write_text(english,encoding='utf-8')
    (ROOT/'docs/BILAN_FR.md').write_text(french,encoding='utf-8')


def run():
    started=time.perf_counter();ids=identities();packages=verify()
    environment=dict(utc=datetime.now(timezone.utc).isoformat(),python=platform.python_version(),platform=platform.platform(),
        dependency_pins=packages,invocation=['python','implementation/run_r4.py'],absolute_host_paths_omitted=True,
        code_id=ids['code_id'],design_id=ids['design_id'])
    write('source_identity.json',ids);write('environment.json',environment)
    path=None;status='FAIL'
    try:
        print('Creating fresh loopback PostgreSQL 18.7 cluster',flush=True)
        path=start()
        core=test_integration.run();write('core_regression.json',dict(**core,execution=environment))
        print(core['log'],flush=True)
        bridge=test_r4.run();write('bridge_regression.json',dict(**bridge,execution=environment))
        print(bridge['log'],flush=True)
        assert core['status']=='PASS' and core['tests']==22 and bridge['status']=='PASS'
        print('Executing 45 predeclared official-SDK sessions',flush=True)
        measured=asyncio.run(pilot.run());write('pilot.json',dict(**measured,execution=environment))
        summary=summarize(measured);write('summary.json',dict(**summary,execution=environment))
        reports(summary,core,bridge,ids)
        assert identities()['code_id']==ids['code_id'] and identities()['design_id']==ids['design_id'],'Inputs changed during run'
        status='PASS_LOCAL_REPRODUCTION'
        result=dict(status=status,core_tests=core['tests'],r4_tests=bridge['tests'],skipped=0,sessions=measured['sessions'],
            comparison_groups=len(measured['comparisons']),elapsed_s=time.perf_counter()-started,**environment)
        write('reproduction.json',result);print(json.dumps(result,indent=2),flush=True)
        return result
    except BaseException as exc:
        folder=ROOT/'.runtime/failures';folder.mkdir(exist_ok=True,parents=True)
        (folder/f'{time.time_ns()}.log').write_text(traceback.format_exc(),encoding='utf-8')
        message=str(exc).replace(str(ROOT),'<WORKSPACE>')
        write('reproduction.json',dict(status='FAIL',error_type=type(exc).__name__,message=message,**environment))
        raise
    finally:
        if path is not None:
            output=stop(path);write('cleanup.json',dict(status='STOPPED',postgres_control=output,code_id=ids['code_id']))


if __name__=='__main__':
    run()
