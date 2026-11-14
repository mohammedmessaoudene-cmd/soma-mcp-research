# SOMA R4: controlled MCP quota pilot

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

Core regression tests: 22; R4 tests: 8; skipped: 0.
The R4 oracle also checks receipt fields, exact JSON types, outbox payload/hash
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

Code ID: `52ad3953d38ac310fd520feceda665c89b057bffacb2d54d775968cde4a058f7`

Design ID: `4d6a1ae6a76f73e75ba35e26019f5015d8e113e9a2ab1efd6c9dd370db80f302`

| Scenario | Arm | RPC/session (3 repetitions) | Read-state/session | Normalized response bytes/session |
|---|---|---|---|---|
| steady | conventional | [13, 13, 13] | [1, 1, 1] | [5937, 5937, 5937] |
| steady | soma | [13, 13, 13] | [1, 1, 1] | [9244, 9244, 9244] |
| steady | conventional_enriched | [13, 13, 13] | [1, 1, 1] | [9166, 9166, 9166] |
| peer_changes | conventional | [17, 17, 17] | [3, 3, 3] | [7219, 7219, 7219] |
| peer_changes | soma | [17, 17, 17] | [3, 3, 3] | [11036, 11036, 11036] |
| peer_changes | conventional_enriched | [17, 17, 17] | [3, 3, 3] | [10946, 10946, 10946] |
| replay_after_peer | conventional | [16, 16, 16] | [2, 2, 2] | [7039, 7039, 7039] |
| replay_after_peer | soma | [14, 14, 14] | [1, 1, 1] | [9962, 9962, 9962] |
| replay_after_peer | conventional_enriched | [14, 14, 14] | [1, 1, 1] | [9878, 9878, 9878] |
| unknown_budget | conventional | [14, 14, 14] | [12, 12, 12] | [5310, 5310, 5310] |
| unknown_budget | soma | [14, 14, 14] | [12, 12, 12] | [8840, 8840, 8840] |
| unknown_budget | conventional_enriched | [14, 14, 14] | [12, 12, 12] | [8756, 8756, 8756] |
| revocation | conventional | [13, 13, 13] | [1, 1, 1] | [5135, 5135, 5135] |
| revocation | soma | [13, 13, 13] | [1, 1, 1] | [7423, 7423, 7423] |
| revocation | conventional_enriched | [13, 13, 13] | [1, 1, 1] | [7369, 7369, 7369] |
