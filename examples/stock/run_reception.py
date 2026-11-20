"""Exercise a fresh copy of the public SOMA CLI with an external adapter."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


CORE_SHA256 = "98a9444ffed3b3475d9efbf9d66f89c55083f434305b886859d4763ad37f4471"
ROOT = Path(__file__).resolve().parent


def save(path, data):
    path.write_bytes((json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode())


def fingerprints():
    paths = sorted((ROOT / "soma_runtime").rglob("*.py"))
    paths += [
        ROOT / name
        for name in (
            "README.md", "ADAPTER_API.md", "NOTICE.md", "mouvements.jsonl", "inputs.json",
            "config.json", "stock_movements.py", "run_reception.py", "reception_oracle.py",
        )
    ]
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def request(observation, action_id, name, rationale):
    return {
        "run_id": observation["run_id"],
        "expected_revision": observation["revision"],
        "observation_id": observation["observation_id"],
        "masked": observation["masked"],
        "action_id": action_id,
        "action": {"name": name, "args": {}},
        "rationale": rationale,
    }


def run(folder):
    folder.mkdir()
    session = folder / "session"
    invocations = []

    def cli(label, operation, *options, refusal=False):
        argv = ["-B", "-m", "soma_runtime", operation, str(session.relative_to(ROOT)), *options]
        with subprocess.Popen(
            [sys.executable, *argv], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as process:
            stdout, stderr = process.communicate()
            invocations.append(
                {
                    "label": label,
                    "argv": argv,
                    "executable": sys.executable,
                    "pid": process.pid,
                    "returncode": process.returncode,
                    "stderr": stderr.decode("utf-8", errors="replace"),
                }
            )
        (folder / f"{label}.json").write_bytes(stdout)
        save(folder / "invocations.json", invocations)
        if (process.returncode != 0) != refusal:
            raise RuntimeError(f"Unexpected CLI status at {label}, see captured output")
        return json.loads(stdout)

    def apply(label, filename, command, **options):
        path = folder / filename
        save(path, command)
        return cli(label, "apply", "--request", str(path.relative_to(ROOT)), **options)

    before = fingerprints()
    save(folder / "sha256-before.json", before)
    if before["soma_runtime/core.py"] != CORE_SHA256:
        raise RuntimeError("Core hash differs from the clean copy provided for reception")

    try:
        cli(
            "01-init", "init", "--adapter", "stock_movements", "--inputs", "inputs.json",
            "--config", "config.json", "--max-actions", "8",
        )
        initial = cli("02-before", "observe")
        sensor = initial["sensors"]["repeated_occurrences"]
        if sensor["value"] <= 0 or "deduplicate" not in [action["name"] for action in initial["actions"]]:
            raise RuntimeError("The controller requires an offered correction and a measured repeat")

        correction = request(
            initial, "movement-correction", "deduplicate",
            f"Deterministic controller: measured {sensor['value']} repeated movement occurrence(s).",
        )
        apply("03-deduplicate", "request-deduplicate.json", correction)
        corrected = cli("04-after-correction", "observe")

        stale = request(initial, "movement-stale", "advance", "Submit the previous observation again")
        cli_label = "05-stale"
        apply(cli_label, "request-stale.json", stale, refusal=True)
        cli("06-after-stale", "observe")

        first = request(corrected, "movement-resume-1", "advance", "Read the next bounded segment")
        apply("07-advance-one", "request-resume-one.json", first)
        checkpoint = cli("08-checkpoint", "observe")
        followup = request(checkpoint, "movement-resume-2", "advance", "Resume at the saved byte cursor")
        apply("09-advance-two", "request-resume-two.json", followup)
        cli("10-final", "observe")

        cli("11-replay", "apply", "--request", str((folder / "request-deduplicate.json").relative_to(ROOT)))
        cli("12-after-replay", "observe")
        cli("13-journal", "journal")
    finally:
        after = fingerprints()
        save(folder / "sha256-after.json", after)
        if after != before:
            raise RuntimeError("Files in the frozen kit or experiment changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", help="name of a new directory next to this script")
    options = parser.parse_args()
    destination = (ROOT / options.evidence).resolve()
    if destination.parent != ROOT:
        parser.error("evidence must be a new directory immediately under the reception copy")
    run(destination)
