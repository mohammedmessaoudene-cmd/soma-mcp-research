# SOMA: transactional MCP quota research

Research preview `0.4.0-research`. A local deterministic quota controller crosses
the official MCP Python SDK into a PostgreSQL transactional boundary. It combines
current authorization, strict inputs, exact incremental capacity, idempotency,
budget exposure, receipts, an outbox and a persistent synthetic receiver.

The experiment compares **optimized conventional receipts**, **SOMA feedback**
and an **information-equivalent conventional enriched response**. The last arm
has the same information as SOMA: matching behavior is expected, not a failure.
No distinctive algorithmic, LLM, customer or production advantage is assumed.

Read the [technical note](docs/TECHNICAL_NOTE_EN.md),
[French results](docs/BILAN_FR.md), [contract](spec/R4_CONTRACT.md) and
[frozen recipes](spec/PILOT_FIXTURES.json). Raw synthetic results are in results/.

## Reproduce on Windows

Python 3.12 and network access are required. From a clean extraction or clone:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\implementation\reproduce.ps1
```

This creates a local venv, installs and verifies exact pins, downloads pinned
PostgreSQL 18.7 Windows binaries from the provider linked by the PostgreSQL
project, verifies their SHA-256, creates a NEW loopback-only cluster, executes
the core and bridge tests plus 45 official-SDK sessions, stops that server, and
checks a public allowlist, leakage patterns and complete manifest. No Windows
service, model API or production data is required. An existing venv is reconciled
against pins. Optional SOMA_PYTHON and SOMA_PG_BIN select already provisioned local
tools; the server version and dependency pins are still verified.

Reproduction overwrites results/docs/manifest with NEW local observations. Keep
a separate copy if comparing against the published frozen results. CI preserves
its own evidence artifact and does not push changed observations back to the repo.
The repository disables newline normalization so source/design identifiers refer
to the exact reviewed file bytes, including the declared fixture line endings.

## What the evidence covers

The unchanged core retains 22 risk-directed R3 regressions. R4 adds permission,
redacted-receipt, pending-request, strict audit and actual SDK server interruption
checks. Five fixed recipes, three arms and three repetitions check both storage
invariants and literal task expectations. Normalized JSON response bytes differ
from raw transport bytes or tokens. No LLM is called by the experiment.

The database owner, host, worker and fixture receiver remain trusted. The receiver
deduplicates both the effect and the logical synthetic charge per event. Providers
charging every attempt require another budget contract. A power failure, real
network outage, multi-host deployment and industrial operation are not qualified.

The new MCP bridge and controller do not import private R3 archives or chat
reviews. Historical provenance is identified by hashes in results/provenance.json;
that earlier private archive is not represented as a publicly accessible artifact.
The published current code is self-contained for the documented reproduction.

License choice is **not yet declared** by the owner; this preview is not described
as open source. Third-party package licenses remain their own; binaries and
dependencies are not redistributed. See [NOTICE](NOTICE.md).
