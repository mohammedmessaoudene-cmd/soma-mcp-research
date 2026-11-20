"""Check the stock result and receipt evidence without importing the adapter."""

import argparse
import json
from itertools import groupby
from operator import itemgetter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORE_SHA256 = "98a9444ffed3b3475d9efbf9d66f89c55083f434305b886859d4763ad37f4471"


def read_json(path):
    return json.loads(path.read_bytes())


def first_occurrences(records):
    key = itemgetter("movement_id")
    return [
        next(group)
        for _, group in groupby(sorted(records, key=key), key=key)
    ]


def aggregate(records):
    key = itemgetter("sku")
    return {
        sku: sum(record["delta"] for record in group)
        for sku, group in groupby(sorted(records, key=key), key=key)
    }


def inspect(folder):
    checks = {}
    lines = (ROOT / "mouvements.jsonl").read_bytes().splitlines(keepends=True)
    records = [json.loads(line) for line in lines]

    def captured(label):
        return read_json(folder / f"{label}.json")

    def check(name, expected, observed):
        checks[name] = {"pass": expected == observed, "expected": expected, "observed": observed}

    before = captured("02-before")
    prefix_count = read_json(ROOT / "config.json")["records_per_step"]
    prefix = records[:prefix_count]
    unique_prefix = first_occurrences(prefix)
    prefix_offset = sum(map(len, lines[:prefix_count]))
    repeats = len(prefix) - len(unique_prefix)
    check("checkpoint_before_end", True, 0 < prefix_count < len(records))
    check("initial_status", "running", before["status"])
    check("initial_position", {"records": prefix_count, "next_byte": prefix_offset}, {
        "records": before["raw"]["records"], "next_byte": before["raw"]["next_byte"],
    })
    check("recalculated_internal_measure", repeats, before["sensors"]["repeated_occurrences"]["value"])
    check("initial_provisional_deltas", aggregate(prefix), before["raw"]["net_deltas"])

    corrected = captured("04-after-correction")
    check("offered_correction", True, "deduplicate" in [action["name"] for action in before["actions"]])
    check("corrected_deltas", aggregate(unique_prefix), corrected["raw"]["net_deltas"])
    check("correction_changed_running_calculation", True, corrected["raw"]["net_deltas"] != before["raw"]["net_deltas"])
    check("correction_did_not_reread_input", prefix_offset, corrected["raw"]["next_byte"])
    check("correction_before_completion", "running", corrected["status"])
    check("one_correction_revision", before["revision"] + 1, corrected["revision"])

    stale = captured("05-stale")
    invocations = {call["label"]: call for call in captured("invocations")}
    check("stale_request_failed", True, invocations["05-stale"]["returncode"] != 0)
    check("stale_has_diagnostic", True, bool(stale))
    check("stale_had_no_effect", corrected, captured("06-after-stale"))

    checkpoint = captured("08-checkpoint")
    after_count = min(2 * prefix_count, len(records))
    checkpoint_offset = sum(map(len, lines[:after_count]))
    check("continued_revision", corrected["revision"] + 1, checkpoint["revision"])
    check("continued_position", checkpoint_offset, checkpoint["raw"]["next_byte"])
    check("continued_deltas", aggregate(first_occurrences(records[:after_count])), checkpoint["raw"]["net_deltas"])
    check("new_process_after_checkpoint", True, invocations["08-checkpoint"]["pid"] != invocations["09-advance-two"]["pid"])
    check("new_process_after_correction", True, invocations["03-deduplicate"]["pid"] != invocations["07-advance-one"]["pid"])
    resume = captured("request-resume-two")
    check("resume_uses_checkpoint_revision", checkpoint["revision"], resume["expected_revision"])
    check("resume_uses_checkpoint_observation", checkpoint["observation_id"], resume["observation_id"])

    final = captured("10-final")
    distinct = first_occurrences(records)
    expected_result = {
        "mode": "deduplicated",
        "records": len(records),
        "unique_movement_ids": len(distinct),
        "net_deltas": aggregate(distinct),
    }
    check("final_revision", checkpoint["revision"] + 1, final["revision"])
    check("final_position", sum(map(len, lines)), final["raw"]["next_byte"])
    check("final_status", "done", final["status"])
    check("final_repeated_occurrences", len(records) - len(distinct), final["sensors"]["repeated_occurrences"]["value"])
    check("uncorrected_result_would_differ", True, aggregate(records) != expected_result["net_deltas"])
    artifacts = sorted((folder / "session").rglob("inventory_delta.json"))
    check("one_published_artifact", 1, len(artifacts))
    if len(artifacts) == 1:
        artifact = read_json(artifacts[0])
        check("published_artifact", expected_result, artifact)
        check("reported_and_published_deltas_agree", expected_result["net_deltas"], final["raw"]["net_deltas"])

    check("identical_replay_receipt", captured("03-deduplicate"), captured("11-replay"))
    check("replay_had_no_effect", final, captured("12-after-replay"))
    before_hashes, after_hashes = captured("sha256-before"), captured("sha256-after")
    check("core_sha256_before", CORE_SHA256, before_hashes["soma_runtime/core.py"])
    check("core_sha256_after", CORE_SHA256, after_hashes["soma_runtime/core.py"])
    check("frozen_files_unchanged", before_hashes, after_hashes)
    return {
        "verdict": "PASS" if all(check["pass"] for check in checks.values()) else "FAIL",
        "checks": checks,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    arguments = parser.parse_args()
    result = inspect(arguments.evidence)
    (arguments.evidence / "oracle-verdict.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"verdict": result["verdict"], "checks": len(result["checks"])}))
    raise SystemExit(0 if result["verdict"] == "PASS" else 1)
