"""Run the local directory delivery or replay its patch into a fresh directory."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
from time import perf_counter
import zipfile

import directory_patch as dp
from soma_runtime import core
from soma_runtime.adapters import diff
from verification.oracles import verify_archive, verify_diff


INTERNAL_ACTION_PREFIX = "soma:auto:"


def reserve(root, requested):
    """Conservative headroom for the next operation, counting logical file lengths."""
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if used + requested > dp.MAX_STORAGE:
        raise ValueError("Managed storage budget exhausted before the next write")
    return used


def verify_tree(directory, expected):
    actual = dp.scan(directory)
    if actual.keys() != expected.keys():
        raise ValueError("Directory file set mismatch")
    size = 0
    for name, target in expected.items():
        left, right = dp.data_at(actual[name], dp.MAX_FILE), dp.data_at(target, dp.MAX_FILE)
        if left != right:
            raise ValueError(f"Directory content differs: {name}")
        size += len(left) + len(right)
    return size


def verify_zip(archive, destination, expected, methods):
    oracle = verify_archive(archive, expected)
    if oracle["verdict"] != "PASS":
        raise ValueError(f"Archive verification failed: {oracle}")
    destination.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = dp.safe_name(member.orig_filename)
            kind = {"compress": zipfile.ZIP_DEFLATED, "store": zipfile.ZIP_STORED}[methods[name]]
            if member.compress_type != kind or member.file_size != expected[name].stat().st_size:
                raise ValueError(f"Archive method or length differs: {name}")
            dp.save(destination / name, bundle.read(member))
    return oracle, verify_tree(destination, expected)


def emit(message, counters):
    line = json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    print(line, flush=True)
    counters["stdio_json_output_bytes_with_lf"] += len(line) + 1


def choose(view):
    names = {choice["name"] for choice in view["actions"]}
    if "continue" in names:
        return "continue"
    if "exact" in names:
        return "exact"
    if {"store", "compress"} == names:
        probe = view["raw"]["probe"]
        return "compress" if probe["deflated_bytes"] < probe["sample_bytes"] else "store"
    if len(names) == 1 and "widen" not in names:
        return next(iter(names))
    raise ValueError("No affordable classical transition")


def command(view, name, args, rationale):
    return {"run_id": view["run_id"], "expected_revision": view["revision"],
            "observation_id": view["observation_id"], "masked": view["masked"],
            "action_id": f'{view["run_id"]}-{view["revision"] + 1}',
            "action": {"name": name, "args": args}, "rationale": rationale}


def step_session(root, session, controller, masked, overhead, counters, policy, label):
    """Persist each checkpoint. A stdio controller must address the emitted observation."""
    view = core.observe(session, masked)
    while True:
        if controller == "stdio":
            emit({"type": "observation", "file": label, "observation": view}, counters)
        if view["status"] == "done":
            return view
        if not view["actions"]:
            raise ValueError(f'No action available: {view["status"]}')
        reserve(root, overhead(view))
        available = {a["name"] for a in view["actions"]}
        if controller == "classic":
            request = command(view, choose(view), {}, "Classical reactive policy")
        elif view["program_id"] == "archive" and "store" in available and "archive" in policy:
            request = command(view, policy["archive"], {}, "Previously selected remaining-member scope")
        elif view["program_id"] == "diff" and "exact" in available and policy.get("diff") == "exact":
            request = command(view, "exact", {}, "Previously selected remaining-file correction")
        elif len(available) == 1 and "widen" not in available:
            request = command(view, next(iter(available)), {}, "Only permitted continuation")
        else:
            decision_key = view["program_id"] + "_external_decisions"
            if counters[decision_key] >= 2:
                raise ValueError("External decision budget exhausted for this effector")
            emit({"type": "command_required", "program_id": view["program_id"],
                  "run_id": view["run_id"], "revision": view["revision"],
                  "reserved_action_id_prefix": INTERNAL_ACTION_PREFIX,
                  "scopes": ["current", "remaining"]}, counters)
            line = sys.stdin.buffer.readline(8193)
            if not line or len(line) > 8192:
                raise ValueError("Missing or oversized controller command (8192 bytes maximum)")
            counters["stdio_json_input_bytes"] += len(line)
            packet = core.read_json(line.decode())
            if not isinstance(packet, dict) or packet.keys() != {"scope", "request"}:
                raise ValueError("Expected scope and core request")
            scope, request = packet["scope"], packet["request"]
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            action_id = request.get("action_id")
            if not isinstance(action_id, str) or not action_id:
                raise ValueError("action_id must be a nonempty string")
            if action_id.startswith(INTERNAL_ACTION_PREFIX):
                raise ValueError("action_id uses the reserved automatic command prefix")
            if (scope not in ("current", "remaining") or request.get("masked") is not masked
                    or request.get("expected_revision") != view["revision"]
                    or request.get("observation_id") != view["observation_id"]):
                raise ValueError("Command must bind the current delivered projection")
            action_object = request.get("action")
            if not isinstance(action_object, dict) or not isinstance(action_object.get("name"), str):
                raise ValueError("request.action must be an object with a string name")
            action = action_object["name"]
            if scope == "remaining" and action not in {"exact", "compress", "store"}:
                raise ValueError("Only exact, compress or store can be persisted")
            receipt = core.apply(session, request)
            account_effect(receipt["effect"], counters)
            if scope == "remaining":
                policy[view["program_id"]] = action
            counters["external_commands_accepted"] += 1
            counters[decision_key] += 1
            dp.save_json(session / f'command-{receipt["revision"]:06d}.json', packet)
            emit({"type": "receipt", "receipt": receipt}, counters)
            view = receipt["after"]
            continue
        request["action_id"] = INTERNAL_ACTION_PREFIX + request["action_id"]
        receipt = core.apply(session, request)
        account_effect(receipt["effect"], counters)
        view = receipt["after"]


def account_effect(effect, counters):
    for name in ("probe_compression_ns", "zip_append_ns", "artifact_hash_read_bytes"):
        if name in effect:
            counters[name] += effect[name]


def freeze(root, old, new, stage, counters):
    left, right = dp.scan(old), dp.scan(new)
    names = sorted(left.keys() | right.keys())
    dp.check_names(names)
    sizes = sum(p.stat().st_size for p in [*left.values(), *right.values()])
    if len(names) > dp.MAX_FILES or sizes > dp.MAX_INPUT:
        raise ValueError("Combined inputs exceed the declared profile")
    reserve(root, sizes + dp.MIB)
    snapshots = {}
    inventory = {}
    for label, tree in (("old", left), ("new", right)):
        folder = stage / label
        folder.mkdir(parents=True)
        snapshots[label], inventory[label] = {}, {}
        for name, source in sorted(tree.items()):
            data = dp.data_at(source, dp.MAX_FILE)
            snapshot = folder / name
            dp.save(snapshot, data)
            snapshots[label][name] = snapshot
            inventory[label][name] = dp.identity(data)
            counters["workflow_snapshot_bytes_each_direction"] += len(data)
            counters["workflow_snapshot_hash_input_bytes"] += len(data)
    dp.save_json(root / "inputs.json", inventory)
    return names, snapshots, inventory


def run(old, new, dest, controller="classic", masked=False):
    old, new, dest = map(dp.absolute, (old, new, dest))
    dp.distinct(old, new, dest)
    if os.path.lexists(dest):
        raise FileExistsError(dest)
    started = perf_counter()
    dest.mkdir()
    stage = dest / "working"
    patch = stage / "patch"
    patch.mkdir(parents=True)
    counters = Counter()
    names, trees, inventory = freeze(dest, old, new, dest / "snapshots", counters)
    old_files, new_files = trees["old"], trees["new"]
    records, oracles, policy = [], {}, {}
    sessions = dest / "sessions"
    sessions.mkdir()
    with core.measure_costs() as core_costs, dp.measure(counters):
        for index, name in enumerate(names):
            src, tgt = inventory["old"].get(name), inventory["new"].get(name)
            record = {"path": name, "source": src, "target": tgt}
            records.append(record)
            if tgt is None:
                record["op"] = "DELETE"
                continue
            b = dp.data_at(new_files[name], dp.MAX_FILE)
            a = dp.data_at(old_files[name], dp.MAX_FILE) if src else None
            counters["workflow_classification_bytes_read"] += len(b) + (len(a) if a is not None else 0)
            if a == b:
                record["op"] = "KEEP"
                continue
            payload = f"objects/{index:06d}"
            record["payload"] = payload
            reason = None if a is None else dp.fallback(a) or dp.fallback(b)
            if a is None or reason:
                record["op"] = "ADD" if a is None else "REPLACE"
                if reason:
                    record["reason"] = reason
                reserve(dest, len(b))
                dp.save(patch / payload, b)
                continue
            n, m = dp.line_count(a), dp.line_count(b)
            rows = 256
            session = sessions / f"diff-{index:06d}"
            config = {"initial_band": abs(n - m), "rows_per_step": rows,
                      "cell_budget": min(diff.MAX_CELLS, dp.MAX_CELLS - counters["dp_cells"])}
            # JSON escaping <=6 bytes per input byte. Step metadata, line records and
            # both projections have fixed headroom under this profile and request limit.
            file_bytes = len(a) + len(b)
            script_ceiling = 6 * file_bytes + 256 * (n + m + 1) + dp.MIB
            def diff_space(view):
                raw = view["raw"]
                return (raw["attempt_cells"] + (n + 1) * (m + 1)
                        + script_ceiling + len(b) + 2 * dp.MIB)
            reserve(dest, file_bytes + (n + 1) * (m + 1) + 2 * dp.MIB)
            core.create_session(session, "soma_runtime.adapters.diff",
                                {"source": old_files[name], "target": new_files[name]}, config, 64)
            view = step_session(dest, session, controller, masked, diff_space, counters, policy, name)
            work = session / view["artifact_root"]
            counters["dp_cells"] += view["raw"]["total_cells"]
            counters["max_final_traceback_file_bytes"] = max(
                counters["max_final_traceback_file_bytes"], (work / "trace.bin").stat().st_size)
            counters["dp_discarded_cells"] += view["raw"]["discarded_cells"]
            counters["dp_recomputed_cells"] += view["raw"]["recomputed_cells"]
            counters["diff_adapter_input_bytes_read"] += view["raw"]["input_bytes_read"]
            counters["oracle_dp_recurrence_cells"] += n * m
            if counters["oracle_dp_recurrence_cells"] > dp.MAX_CELLS:
                raise ValueError("Independent full-distance verification budget exhausted")
            oracle = verify_diff(old_files[name], new_files[name], work / "patch.json", work / "reconstructed.txt")
            if oracle["verdict"] != "PASS":
                raise ValueError(f"Diff verification failed: {oracle}")
            oracles[name] = oracle
            reserve(dest, (work / "patch.json").stat().st_size)
            dp.save(patch / payload, dp.data_at(work / "patch.json", script_ceiling))
            record["op"] = "LINE_PATCH"
        reserve(dest, 2 * dp.MIB + sum(v["bytes"] for v in inventory["new"].values()))
        dp.save_json(patch / "manifest.json", {"schema": "soma.directory-patch.v1", "operations": records})
        reconstructed = stage / "reconstructed"
        dp.replay(dest / "snapshots" / "old", patch, reconstructed)
        counters["directory_compare_bytes_read"] += verify_tree(reconstructed, new_files)
        archive_inputs = dp.scan(reconstructed)
        size = sum(p.stat().st_size for p in archive_inputs.values())
        session = sessions / "archive"
        reserve(dest, size + 2 * dp.MIB)
        core.create_session(session, "soma_runtime.adapters.archive", archive_inputs,
                            {"probe_bytes": 65536, "chunk_bytes": 65536, "read_budget": 2 * size},
                            2 * len(archive_inputs) + 1)
        def archive_space(view):
            raw = view["raw"]
            member = raw["current_member"]
            return raw["archive_bytes"] + (2 * member["size"] if member else 0) + 2 * dp.MIB
        view = step_session(dest, session, controller, masked, archive_space, counters, policy, None)
        raw = view["raw"]
        methods = {r["name"]: r["method"] for r in raw["completed_members"]}
        counters["archive_probe_input_bytes"] = raw["probe_work_bytes"]
        counters["archive_member_input_bytes"] = raw["member_work_bytes"]
        counters["archive_retained_zip_bytes"] = raw["archive_bytes"]
        reserve(dest, raw["archive_bytes"] + size + 2 * dp.MIB)
        shutil.copyfile(session / view["artifact_root"] / "bundle.zip", stage / "bundle.zip")
        oracle, compared = verify_zip(stage / "bundle.zip", stage / "extracted", new_files, methods)
        counters["directory_compare_bytes_read"] += compared
    # Check the two original trees still match the bytes frozen for this delivery.
    counters["directory_compare_bytes_read"] += verify_tree(old, old_files) + verify_tree(new, new_files)
    counters.update(core_costs)
    counters["managed_file_bytes_before_report"] = reserve(dest, 2 * dp.MIB)
    report = {"verdict": "PASS", "controller": controller, "masked": masked,
              "diff_oracles": oracles, "archive_oracle": oracle, "archive_methods": methods,
              "costs": dict(counters), "wall_seconds_through_checks": perf_counter() - started,
              "physical_io_bytes": "NOT_MEASURED", "peak_rss": "NOT_MEASURED",
              "model_usage": "NOT_MEASURED" if controller == "stdio" else "NO_MODEL",
              "source_files": {}, "storage_limit_logical_file_bytes": dp.MAX_STORAGE}
    kit = Path(__file__).resolve().parent
    for path in [Path(__file__), Path(dp.__file__), kit / "verification" / "oracles.py",
                 *(kit / "soma_runtime").rglob("*.py")]:
        report["source_files"][path.relative_to(kit).as_posix()] = sha256(path.read_bytes()).hexdigest()
    dp.save_json(stage / "report.json", report)
    stage.rename(dest / "result")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, positional in (("run", ("old", "new", "destination")),
                             ("replay", ("old", "patch", "destination"))):
        task = sub.add_parser(name)
        for key in positional:
            task.add_argument(key, type=Path)
        if name == "run":
            task.add_argument("--controller", choices=["classic", "stdio"], default="classic")
            task.add_argument("--masked", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "run":
            result = run(args.old, args.new, args.destination, args.controller, args.masked)
            print(json.dumps({"type": "result", "verdict": result["verdict"],
                              "directory": str(args.destination / "result")}))
        else:
            print(json.dumps(dp.replay(args.old, args.patch, args.destination)))
    except (KeyError, OSError, ValueError, TypeError, RuntimeError) as error:
        print(json.dumps({"type": "error", "reason": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
