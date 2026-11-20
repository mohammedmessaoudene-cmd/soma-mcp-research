"""Build an exact ZIP, observing a real compression probe before each member.

The probe is a prefix experiment, not a prediction guarantee for the remainder.
No previously published archive is overwritten. All writes go to the revision stage.
"""
from collections import Counter
from hashlib import sha256
import math
from pathlib import Path, PurePosixPath
from time import perf_counter_ns
import zipfile
import zlib


INFO = {
    "purpose": "Assemble captured files into an exact ZIP; choose each member's method after a real prefix probe.",
    "inputs": "Named files; config.members maps every input name to a unique relative archive name.",
    "actions": {"probe": "Compress a bounded prefix and observe its bytes.",
                "compress": "Read the whole current file and append it using DEFLATE.",
                "store": "Read the whole current file and append it without compression.",
                "finish": "Close the assembly and expose bundle.zip."},
    "limits": "Cooperative trusted adapter. Prefix compressibility does not guarantee whole-file compressibility.",
}


def integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}")
    return value


def _measurement(value, unit, source, cost, *, valid=True, limitation=""):
    return {"value": value, "unit": unit, "source": source, "valid": valid,
            "computation_cost": cost, "limitation": limitation}


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("Archive names must be relative POSIX paths")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError("Unsafe archive name")
    return name


def create(config, inputs, stage):
    if not isinstance(config, dict):
        raise ValueError("Expected archive configuration")
    allowed = {"members", "probe_bytes", "chunk_bytes", "read_budget"}
    if set(config) - allowed:
        raise ValueError("Unknown archive configuration")
    members = config.get("members", {key: key for key in inputs})
    if not isinstance(members, dict) or set(members) != set(inputs):
        raise ValueError("members must cover every captured input exactly once")
    names = [_safe_name(name) for name in members.values()]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate archive name")
    order = [{"input": key, "name": members[key], "size": Path(inputs[key]).stat().st_size}
             for key in sorted(inputs)]
    total = sum(item["size"] for item in order)
    settings = {
        "probe_bytes": integer(config.get("probe_bytes", 16384), "probe_bytes", 1, 1048576),
        "chunk_bytes": integer(config.get("chunk_bytes", 65536), "chunk_bytes", 1, 1048576),
        "read_budget": integer(config.get("read_budget", 2 * total), "read_budget"),
    }
    # Creating an empty valid ZIP is initialization work, not a completed delivery.
    with zipfile.ZipFile(Path(stage) / "bundle.zip", "x"):
        pass
    return {"config": settings, "members": order, "index": 0, "phase": "probe" if order else "finish",
            "status": "running", "probe": None, "io_read_bytes": 0,
            "probe_work_bytes": 0, "member_work_bytes": 0, "completed": [],
            "archive_bytes": (Path(stage) / "bundle.zip").stat().st_size,
            "measurement_operations": 0}


def observe(state, inputs, workdir):
    current = state["members"][state["index"]] if state["index"] < len(state["members"]) else None
    probe = state["probe"]
    raw = {"phase": state["phase"], "member_index": state["index"],
           "member_count": len(state["members"]), "current_member": current,
           "read_budget": state["config"]["read_budget"], "io_read_bytes": state["io_read_bytes"],
           "probe_work_bytes": state["probe_work_bytes"], "member_work_bytes": state["member_work_bytes"],
           "archive_bytes": state["archive_bytes"], "completed_members": state["completed"],
           "probe": probe, "measurement_operations": state["measurement_operations"]}
    sensors = {
        "remaining_members": _measurement(len(state["members"]) - state["index"], "members",
            "completed member transitions", {"integer_subtractions": 1}),
        "read_margin": _measurement(state["config"]["read_budget"] - state["io_read_bytes"], "bytes",
            "accumulated actual file reads", {"integer_subtractions": 1}),
    }
    if probe is not None:
        n = probe["sample_bytes"]
        counts = probe["symbol_counts"]
        entropy = -sum((v / n) * math.log2(v / n) for v in counts.values()) if n else None
        gain = 1 - probe["deflated_bytes"] / n if n else None
        sensors["prefix_entropy"] = _measurement(entropy, "bits/byte", "observed prefix byte histogram",
            {"log2_evaluations": len(counts)}, valid=n > 0,
            limitation="Prefix distribution only; not a proof of incompressibility or semantic quality.")
        sensors["prefix_compression_gain"] = _measurement(gain, "fraction", "actual raw-DEFLATE prefix output",
            {"compressor_input_bytes": n, "division": int(n > 0)}, valid=n > 0,
            limitation="Finished prefix stream includes its framing cost and does not predict all remaining bytes.")
    names = {"probe": ["probe"], "choose": ["compress", "store"], "finish": ["finish"]}.get(state["phase"], [])
    actions = [{"name": name, "description": INFO["actions"][name], "args": {}} for name in names]
    return {"status": state["status"], "raw": raw, "sensors": sensors, "actions": actions}


def step(state, action, inputs, stage):
    if not isinstance(action, dict) or set(action) != {"name", "args"} or action["args"] != {}:
        raise ValueError("Archive commands require a name and empty args")
    name = action["name"]
    allowed = {a["name"] for a in observe(state, inputs, stage)["actions"]}
    if name not in allowed:
        raise ValueError("Action not available at this checkpoint")
    effect = {"action": name, "read_bytes": 0, "changed": {}, "artifacts": []}
    if name == "finish":
        artifact = Path(stage) / "bundle.zip"
        digest = sha256()
        hashed = 0
        with artifact.open("rb") as stream:
            while chunk := stream.read(state["config"]["chunk_bytes"]):
                digest.update(chunk)
                hashed += len(chunk)
        state.update(phase="done", status="done")
        effect["artifacts"] = [{"path": "bundle.zip", "size": artifact.stat().st_size,
                                "sha256": digest.hexdigest()}]
        effect["artifact_hash_read_bytes"] = hashed
        effect["changed"] = {"status": "done"}
        return state, effect
    member = state["members"][state["index"]]
    source = Path(inputs[member["input"]])
    required = min(member["size"], state["config"]["probe_bytes"]) if name == "probe" else member["size"]
    if state["io_read_bytes"] + required > state["config"]["read_budget"]:
        state.update(phase="failed", status="failed")
        effect["changed"] = {"status": "failed", "reason": "read_budget_exhausted"}
        return state, effect
    if name == "probe":
        with source.open("rb") as stream:
            sample = stream.read(required)
        if len(sample) != required:
            raise ValueError("Captured source size changed")
        started = perf_counter_ns()
        compressor = zlib.compressobj(level=6, wbits=-15)
        compressed = compressor.compress(sample) + compressor.flush()
        effect["probe_compression_ns"] = perf_counter_ns() - started
        counts = Counter(sample)
        state["probe"] = {"sample_bytes": len(sample), "deflated_bytes": len(compressed),
                          "sample_sha256": sha256(sample).hexdigest(),
                          "symbol_counts": {str(k): v for k, v in sorted(counts.items())}}
        state["phase"], state["status"] = "choose", "waiting"
        state["probe_work_bytes"] += len(sample)
        state["measurement_operations"] += len(sample) + len(counts)
        effect["changed"] = {"phase": "choose", "sample_bytes": len(sample), "deflated_bytes": len(compressed)}
        read = len(sample)
    else:
        compression = zipfile.ZIP_DEFLATED if name == "compress" else zipfile.ZIP_STORED
        info = zipfile.ZipInfo(member["name"], date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = compression
        info.file_size = member["size"]
        read = 0
        started = perf_counter_ns()
        with zipfile.ZipFile(Path(stage) / "bundle.zip", "a", compression=compression, compresslevel=6) as archive:
            if member["name"] in archive.namelist():
                raise ValueError("Member already present")
            with source.open("rb") as src, archive.open(info, "w", force_zip64=member["size"] >= 2**31) as dst:
                while chunk := src.read(state["config"]["chunk_bytes"]):
                    if read + len(chunk) > member["size"]:
                        raise ValueError("Captured source size changed")
                    dst.write(chunk)
                    read += len(chunk)
            if read != member["size"]:
                raise ValueError("Captured source size changed")
            written = archive.getinfo(member["name"])
            record = {"name": member["name"], "method": name, "input_bytes": read,
                      "compressed_bytes": written.compress_size, "crc32": written.CRC}
        effect["zip_append_ns"] = perf_counter_ns() - started
        state["completed"].append(record)
        state["index"] += 1
        state["probe"] = None
        state["phase"] = "probe" if state["index"] < len(state["members"]) else "finish"
        state["status"] = "running"
        state["member_work_bytes"] += read
        effect["changed"] = {"member": record, "phase": state["phase"]}
    state["io_read_bytes"] += read
    state["archive_bytes"] = (Path(stage) / "bundle.zip").stat().st_size
    effect["read_bytes"] = read
    return state, effect
