# SOMA R4 — controlled quota pilot and research publication

Revision 2, 2026-11-14. Local deterministic controller; no production or LLM claim.

The PostgreSQL admission, permissions, arithmetic, receipts, reservation and
outbox core is copied unchanged from the frozen local R3. Its tests are rerun.
Only a host bridge and a deterministic quota client are added. The official MCP
SDK crosses a separate stdio server in all three experimental arms.

## Arms and fairness

1. `conventional`: compact receipts; an optimized controller propagates known
   exact own effects and only queries state when necessary. It is not forced to
   perform an avoidable read after every mutation.
2. `soma`: the same receipts and current permitted feedback in the experimental
   `org.messaoudene.soma/feedback` metadata namespace.
3. `conventional_enriched`: identical permitted current observations in a generic
   application metadata namespace. This information-equivalent control can supply
   the same behavior without a SOMA-specific algorithm.

All arms share procedures, privilege boundary, synthetic cost bound 60, receiver
charge 40, controller policy, task trace and environment interventions. Scenario
changes are scheduled by logical step, independent of RPC count or wall time.
No design conclusion assumes SOMA wins. B and C should match after namespace
normalization; conventional minimal may need a state read after a historical
receipt replay, because a receipt's commit version need not be the current version.

## Controller and scenarios

Predeclared quota deltas: [4,4,4,-3,6,-4,8,-5,2,-2,1,-1]. Start account a=40,
b=50, total=90, cap=100. The controller clips positive deltas to known remaining
capacity, skips zero, propagates admitted own deltas and conservative exposure,
and uses exact request replay after uncertain outcome. It does not trust agent
claims or generate SQL. A confirmed VERSION_CONFLICT creates no receipt and can
refresh expected_version before retrying that still-uncommitted logical key.

Five scenarios, three arms, three repetitions (45 sessions): stable; peer changes;
historical replay after peer advances state; UNKNOWN/settlement with budget 100;
complete disable/re-enable. Normal scenarios use budget 10000. An independent
snapshot oracle verifies accounts, total, versions, receipts, reservations, outbox
and spent/held/unknown at checkpoints. The same logical outcomes and final business
state are required; separate RPC/refusal counts quantify protocol consequences.

Feedback authorization remains entirely at the SQL boundary: enabled, mutate,
read_receipt and read_state are distinct. The bridge only renames permitted data.
No client-supplied role, cost, tenant, settlement, lease or server failpoint exists.

## Actual MCP server interruption

A separate TEST-ONLY server wrapper pauses after the adapter has committed and
before responding to a real SDK call. Its interpreter PID is attested to a local
marker. The harness terminates that exact process, observes the actual SDK
transport failure, restarts a server, and replays the SAME request/key. One effect,
receipt, reserve and event must remain. This is process interruption, not a power
failure test. It complements the retained R3 before/after-commit process tests.

## Measurement and publication boundary

Archive requests, results, normalized response JSON bytes, content-projection
bytes, semantic outcomes, real server protocol version, and timings with setup,
audit and receiver costs separated. Reconstructed JSON bytes are not raw network
bytes or tokens. No LLM invocation, user study or customer integration is measured.

Only a reviewed allowlist is public: current sources/tests/schema, pinned
dependencies, reproducibility command, normalized synthetic results, technical
note, CI workflow and checksums. Runtime credentials/databases, private chat
transcripts, user paths and historical private archives stay private. The public
experiment must reproduce without those archives. A fresh public Git history is
created only after review. No license grant is inferred from absence of a license.

## Trust and limits

Host, adapter, database engine, schema owner, worker and fixture receiver remain
trusted. Complete mediation is assumed and tested against the application roles.
Provider effect AND logical charge deduplicate per event. Different provider
billing needs another budget contract. A client pilot, monetary value, independent
institutional reproduction, LLM improvement, worldwide novelty and production
qualification remain unestablished. An observed public CI is a second environment
execution of these tests, not independent research adoption.

## Normative controller policy (R4-A01 closure)

The cache is (business version, capacity remaining, conservatively available
budget); initial own account balance 40 is trusted PUBLIC FIXTURE configuration,
not an extra read of protected runtime state. Only current proposal, saved own
requests/receipts, this memory and authorized MCP observations enter the policy.
The controller never receives oracle rows or administrative/peer event results.

For each proposal, at most four admission attempts are allowed:

1. If cache is absent, read_state once. If unauthorized, end this logical step.
2. If cached budget is below 60, read_state. If it remains below 60, terminate
   SKIPPED_BUDGET. A business version never implies budget freshness.
3. Positive delta=min(proposal,cached capacity); negative delta=max(proposal,
   -known own balance). If this is zero, refresh state ONCE per logical proposal,
   recompute and terminate SKIPPED_ZERO only if it is still zero. This covers
   capacity freed by a peer after the last observation.
4. Send the fixed logical key with this delta and cached expected_version.
5. A new COMMITTED receipt updates own balance/history ONCE. With feedback, use
   its current observation; without feedback, propagate known delta, the receipt
   commit version and conservative available_budget-=60. No forced read follows.
6. A historical exact replay adds NO effect and never overwrites a newer cache
   with its historical commit version. Permitted current feedback may update the
   cache. Without it, retain the optimistic cache; a later VERSION_CONFLICT can
   reveal that a peer advanced the state.
7. Confirmed VERSION_CONFLICT: read_state, recompute candidate and retry the SAME
   still-uncommitted logical key, bounded by four admission attempts. This is
   permitted because the confirmed refusal created no receipt. Other confirmed
   authorization refusal is terminal for that step; the next declared step may
   try again under whatever authority then exists, using the preserved cache.
8. An OUTCOME_PENDING result preserves the entire request. Query status of that
   exact key. COMMITTED registers that known effect once; otherwise return
   OUTCOME_PENDING without a new key or changed arguments. Authorized current
   feedback can update cache; absent feedback, refresh before a future new effect.
   An actual broken SDK session is handled by the separate recovery harness:
   restart server/session, query and replay the saved exact request, never modify it.

The exact request is placed in an in-memory pending ledger BEFORE dispatch. A
transport exception leaves it pending; an unresolved entry blocks any new logical
key, including at a later proposal. Only observed COMMITTED status registers the
effect once and clears it. NOT_OBSERVED_OR_PENDING and authorization loss never
prove absence of effect. This ledger is not claimed durable across a client
power failure. The actual SDK test preserves the saved request in its harness,
then starts a new OS client and a new attested server for exact replay.

read_state refresh and authorization failure do not themselves mutate business
state. The controller has no hidden hook saying a peer/worker changed data. The
only extra client instruction is the recipe's scheduled exact replay of its own
saved request. Host event outcomes are audit data, not controller input.

Every RPC records its transition reason. B/C comparison renames only the two
feedback namespace keys to CURRENT_OBSERVATION, preserving all values/content.
Logical outcome equality compares step,status,effective delta and commit version
when present. All business checkpoints are also compared across arms. Timing,
process IDs and namespace byte-length differences are not logical decisions.
Normalized bytes use json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=True)
on the SDK result model_dump(by_alias=True,exclude_none=True), encoded UTF-8.
Initialization/tools-list are setup overhead, separate from tool RPC counts;
every intermediate refusal/replay is counted as an RPC, separate from terminal
logical outcomes. Three repetitions are descriptive, not independent tasks.

## Frozen recipes and task oracle (R4-A02 closure)

spec/PILOT_FIXTURES.json is normative and included in design_id. It fixes initial
state, each before-step event, effective deltas, terminal statuses and all twelve
expected business/accounting checkpoints. A literal task oracle checks those
values independently of the controller, in addition to the full integrity oracle.

Each session fully resets tenants, subjects, accounts, operations, reserves and
outbox, and uses a new receiver file. Every before-step event executes once,
outside the controller retry loop. Peer writes use role soma_peer, account b,
delta -1, and the ordinary guarded SQL procedure with its then-current version.
No worker runs except the exact declared mark_unknown/settle events. Other outbox
events remain HELD. All fixtures use two tenants and 128 accounts per tenant.

| Scenario | Events before proposal k (zero-based) | Final a,b,total,version,spent,held,unknown |
|---|---|---|
| steady | none | 45,50,95,12,0,720,0 |
| peer_changes | peer -1 at k=3 and k=7 | 46,48,94,14,0,840,0 |
| replay_after_peer | at k=2: peer -1, then exact replay of task_0 | 46,49,95,13,0,780,0 |
| unknown_budget | mark task_0 UNKNOWN at1; settle at3; mark task_3 UNKNOWN at5; settle at7 | 41,50,91,2,80,0,0 |
| revocation | enabled=false at4; enabled=true at8; other rights stay true | 47,50,97,8,0,480,0 |

Normal budgets are 10000; unknown_budget uses 100. Expected deltas/statuses and
policy versions at every checkpoint are literal data in the recipe. These are
pre-execution arithmetic expectations, NOT measured successes. A mismatch is a
failed task even if all arms agree or the storage invariants remain safe.

## Publication sequence

The exact public allowlist, code/design IDs, report and manifest are reviewed
BEFORE publication; R411 remains NOT_RUN. After candidate approval, create a fresh
Git history in the user's new repository, publish that content and record the
actual commit. Observe CI on that commit, then create the prerelease and verify
the downloaded asset hash. Only then may R411 and overall publication be PASS.
No original private R3 Git history or conversation is included. The public
manifest links reviewed content to the eventual commit and archive; no retroactive
claim that the candidate review already observed a publication is permitted.
