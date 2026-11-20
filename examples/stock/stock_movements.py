"""Cooperative reconciliation of inventory movements from a JSON Lines export."""

import json
from pathlib import Path


INFO = {
    "name": "stock_movements",
    "objective": "Reconcile stock increments by occurrence ID",
    "inputs": ["movements"],
    "actions": ["advance", "deduplicate"],
}


def _accumulate(totals, sku, delta):
    totals[sku] = totals.get(sku, 0) + delta


def _scan(state, path):
    scanned = 0
    suppressed = 0
    start = state["next_offset"]
    with path.open("rb") as stream:
        stream.seek(start)
        for _ in range(state["records_per_step"]):
            line = stream.readline()
            if not line:
                break
            record = json.loads(line)
            if not isinstance(record, dict) or record.keys() != {"movement_id", "sku", "delta"}:
                raise ValueError("Each record must contain movement_id, sku and delta")
            movement_id, sku, delta = record["movement_id"], record["sku"], record["delta"]
            if not isinstance(movement_id, str) or not movement_id:
                raise ValueError("movement_id must be a nonempty string")
            if not isinstance(sku, str) or not sku or type(delta) is not int:
                raise ValueError("Expected a nonempty SKU and an integer stock increment")
            movement = [sku, delta]
            previous = state["seen"].get(movement_id)
            if previous is None:
                state["seen"][movement_id] = movement
            else:
                if movement != previous:
                    raise ValueError(f"Conflicting records for movement {movement_id!r}")
                state["repeat_count"] += 1
                if state["mode"] == "deduplicated":
                    suppressed += 1
                    scanned += 1
                    continue
                _accumulate(state["reversal"], sku, delta)
            _accumulate(state["net"], sku, delta)
            scanned += 1
        state["next_offset"] = stream.tell()
    state["records"] += scanned
    return {"records_read": scanned, "bytes_read": state["next_offset"] - start, "suppressed": suppressed}


def _complete(state, stage):
    if state["next_offset"] < state["size_bytes"]:
        return []
    state["status"] = "done"
    result = {
        "mode": state["mode"],
        "records": state["records"],
        "unique_movement_ids": len(state["seen"]),
        "net_deltas": state["net"],
    }
    (stage / "inventory_delta.json").write_bytes(
        (json.dumps(result, sort_keys=True) + "\n").encode("utf-8")
    )
    return ["inventory_delta.json"]


def create(config: dict, inputs: dict[str, Path], stage: Path) -> dict:
    limit = config.get("records_per_step")
    if config.keys() != {"records_per_step"} or type(limit) is not int or limit < 1:
        raise ValueError("records_per_step must be a positive integer")
    if inputs.keys() != {"movements"}:
        raise ValueError("Expected one movement file")
    path = inputs["movements"]
    state = {
        "records_per_step": limit,
        "size_bytes": path.stat().st_size,
        "status": "running",
        "mode": "provisional",
        "next_offset": 0,
        "records": 0,
        "repeat_count": 0,
        "seen": {},
        "net": {},
        "reversal": {},
    }
    _scan(state, path)
    _complete(state, stage)
    return state


def observe(state: dict, inputs: dict[str, Path], workdir: Path) -> dict:
    actions = []
    if state["status"] == "running":
        actions.append({"name": "advance", "description": "Continue at the next byte", "args": {}})
        if state["mode"] == "provisional" and state["repeat_count"]:
            actions.append(
                {
                    "name": "deduplicate",
                    "description": "Reverse provisional repeated movements and suppress future repeats",
                    "args": {},
                }
            )
    return {
        "status": state["status"],
        "raw": {
            "records": state["records"],
            "next_byte": state["next_offset"],
            "size_bytes": state["size_bytes"],
            "net_deltas": dict(state["net"]),
            "mode": state["mode"],
        },
        "sensors": {
            "repeated_occurrences": {
                "value": state["repeat_count"],
                "unit": "records",
                "source": "Occurrence IDs looked up as each input record is read",
                "validity": "Complete input records through next_byte",
                "limits": "Repeats are measured only in the consumed prefix",
                "cost": {"id_lookups": state["records"]},
            }
        },
        "actions": actions,
    }


def step(state: dict, action: dict, inputs: dict[str, Path], stage: Path) -> tuple[dict, dict]:
    if state["status"] != "running" or action.get("args") != {}:
        raise ValueError("Expected an argument-free action at a running checkpoint")
    name = action.get("name")
    if name == "advance":
        work = _scan(state, inputs["movements"])
        return state, {"change": name, "work": work, "artifacts": _complete(state, stage)}
    if name == "deduplicate" and state["mode"] == "provisional" and state["repeat_count"]:
        reversal = state["reversal"]
        for sku, delta in reversal.items():
            _accumulate(state["net"], sku, -delta)
        state["mode"] = "deduplicated"
        state["reversal"] = {}
        return state, {
            "change": name,
            "reversed_deltas": reversal,
            "work": {"records_read": 0, "bytes_read": 0, "skus_corrected": len(reversal)},
            "artifacts": [],
        }
    raise ValueError("Action unavailable at this checkpoint")
