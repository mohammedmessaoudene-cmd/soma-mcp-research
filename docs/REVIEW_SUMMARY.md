# Review scope and closed findings

This document records the distinct AI code review of the R4 candidate, not
academic peer review or institutional adoption. The publication/CI decision is
recorded separately in the actual release metadata; private chat transcripts
and local runtime credentials are not published.

Reviewed code ID:
`52ad3953d38ac310fd520feceda665c89b057bffacb2d54d775968cde4a058f7`

Reviewed design ID:
`4d6a1ae6a76f73e75ba35e26019f5015d8e113e9a2ab1efd6c9dd370db80f302`

Three concrete findings were reproduced and closed by the reviewer:

| Finding | Correction | Verification |
|---|---|---|
| Stale-capacity test exhausted its synthetic budget before reaching the intended event | Use a sufficient fixture budget | Targeted scenario and regression suite pass |
| A legally redacted new receipt omits committed_version | Infer only the known own commit version from expected_version+1; retain redacted response unchanged | Three real SDK arms, receipt rights removed/restored, exact replay and next effect checked |
| An integrity-only oracle did not validate receipt/payload content and Python equality merged bool/int | R4 snapshot audit checks exact JSON serialization types, receipt fields, payload hash and effect ledger | Five independent corruptions rejected after positive fixtures: receipt true/1.0, payload true/1.0 with recomputed hash, invalid hash |

The original R3 core files were not altered. Privileged corruption tests exercise
audit detection; they do not claim the trusted schema owner is an adversary
blocked by the service.

On the frozen candidate, the reviewer independently recalculated 29 source/design
hashes, 45 final sessions and 540 task checkpoints, all 15 SOMA/enriched comparisons,
645 tool RPCs and 369780 normalized response bytes. The final author run had
22 core and eight R4 tests passing with no skip. Thirty earlier reviewer-run
development sessions on two versions remain development evidence and are not
relabeled as these 45 final sessions.

The allowlist/privacy review found no private conversation URL, personal absolute
path, known generated credential value or detected token/key pattern in the public
files. It is a bounded release audit, not a universal absence-of-secrets theorem.
The same exact sources must be bound to the eventual public commit and CI.

The conventional enriched response is intentionally information-equivalent to
SOMA. No distinct algorithmic, safety, token, LLM or commercial superiority is
established. A real workflow owner and operational integration remain future work.
