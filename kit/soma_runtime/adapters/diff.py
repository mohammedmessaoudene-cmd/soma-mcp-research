"""Checkpointed unit-cost line edit distance using diagonal banding.

The band threshold argument follows Ukkonen, Information and Control (1985).
All paths costing at most k stay within |i-j| <= k. A path leaving this
strip costs at least k+1, even if it comes back into the strip afterwards.
"""

import hashlib
import json
from pathlib import Path


MAX_LINES = 8192
MAX_BYTES = 4 * 1024 * 1024
MAX_CELLS = 8_000_000
TRACE = "trace.bin"
DIAGONAL, DELETE, INSERT = 1, 2, 3

_COMMANDS = {
    "continue": ("Compute the next bounded group of source rows.", {}),
    "widen": ("Restart from row zero with a larger threshold.", {"band": "integer"}),
    "exact": ("Restart from row zero in the full matrix.", {}),
    "finish": ("Trace back the certified minimum and reconstruct the target.", {}),
}
INFO = {
    "inputs": {"source": "UTF-8 source text", "target": "UTF-8 target text"},
    "objective": "Produce a minimum unit-cost edit script on whole lines including endings.",
    "actions": list(_COMMANDS),
}


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _load(path: Path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"input exceeds {MAX_BYTES} bytes")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("input grew beyond the size limit")
    text = data.decode("utf-8")
    chunks = text.split("\n")
    lines = [part + "\n" for part in chunks[:-1]]
    if chunks[-1]:
        lines.append(chunks[-1])
    if len(lines) > MAX_LINES:
        raise ValueError(f"input exceeds {MAX_LINES} lines")
    return data, lines


def _span(i, m, band):
    return max(0, i - band), min(m, i + band)


def _width(i, m, band):
    low, high = _span(i, m, band)
    return high - low + 1


def _restart(s, stage, band, mode):
    # Bands only grow. At a given row all earlier explored intervals nest.
    # Their union width is enough to count actual repeated cell evaluations.
    covered = list(s["previous_widths"])
    discarded = s["attempt_cells"]
    for i in range(s["row"] + 1):
        covered[i] = max(covered[i], _width(i, s["target_lines"], s["band"]))
    width = _width(0, s["target_lines"], band)
    (stage / TRACE).write_bytes(bytes([0]) + bytes([INSERT]) * (width - 1))
    s.update(
        row=0, band=band, mode=mode, frontier=list(range(width)), previous_widths=covered,
        attempt=s["attempt"] + 1, attempt_cells=width, total_cells=s["total_cells"] + width,
        recomputed_cells=s["recomputed_cells"] + covered[0],
        discarded_cells=s["discarded_cells"] + discarded,
    )


def create(config: dict, inputs: dict[str, Path], stage: Path) -> dict:
    if type(config) is not dict or set(config) - {"initial_band", "rows_per_step", "cell_budget"}:
        raise ValueError("unknown diff configuration")
    if set(inputs) != {"source", "target"}:
        raise ValueError("expected exactly source and target inputs")
    source, a = _load(inputs["source"])
    target, b = _load(inputs["target"])
    n, m = len(a), len(b)
    gap = abs(n - m)
    band = _integer(config.get("initial_band", gap), "initial_band", gap, max(n, m))
    rows = _integer(config.get("rows_per_step", 64), "rows_per_step", 1, 256)
    budget = _integer(config.get("cell_budget", min(MAX_CELLS, 2 * (n + 1) * (m + 1))),
                      "cell_budget", _width(0, m, band), MAX_CELLS)
    state = {
        "config": {"initial_band": band, "rows_per_step": rows, "cell_budget": budget},
        "source_lines": n, "target_lines": m, "source_sha256": hashlib.sha256(source).hexdigest(),
        "target_sha256": hashlib.sha256(target).hexdigest(), "input_bytes_read": len(source) + len(target),
        "previous_widths": [0] * (n + 1), "row": -1, "band": band, "attempt": 0,
        "attempt_cells": 0, "total_cells": 0, "discarded_cells": 0, "recomputed_cells": 0,
        "finished": False,
    }
    _restart(state, stage, band, "banded")
    return state


def _lower_bound(s):
    # Every path inside the band crosses the current row at some j. Its suffix
    # must pay at least the difference between the remaining sequence lengths.
    i, n, m, k = s["row"], s["source_lines"], s["target_lines"], s["band"]
    low, _ = _span(i, m, k)
    bound = min(cost + abs((n - i) - (m - j)) for j, cost in enumerate(s["frontier"], low))
    return min(k + 1, bound)


def _choices(s, bound):
    if s["finished"]:
        return "done", []
    i, n, m, k = s["row"], s["source_lines"], s["target_lines"], s["band"]
    if i == n and s["frontier"][-1] <= k:
        return "waiting", ["finish"]
    remaining = s["config"]["cell_budget"] - s["total_cells"]
    options = []
    if i < n and bound <= k and _width(i + 1, m, k) <= remaining:
        options.append("continue")
    if k < max(n, m):
        if _width(0, m, k + 1) <= remaining:
            options.append("widen")
        if _width(0, m, max(n, m)) <= remaining:
            options.append("exact")
    if not options:
        return "failed", []
    return ("running" if "continue" in options else "waiting"), options


def observe(state: dict, inputs: dict[str, Path], workdir: Path) -> dict:
    s = state
    bound = _lower_bound(s)
    status, names = _choices(s, bound)
    low, _ = _span(s["row"], s["target_lines"], s["band"])
    raw = {key: s[key] for key in (
        "source_lines", "target_lines", "row", "mode", "band", "attempt", "attempt_cells",
        "total_cells", "discarded_cells", "recomputed_cells", "input_bytes_read",
        "source_sha256", "target_sha256",
    )}
    raw.update(config=dict(s["config"]), frontier_start_column=low, frontier_costs=list(s["frontier"]))
    certified = s["row"] == s["source_lines"] and s["frontier"][-1] <= s["band"]
    sensors = {
        "distance_lower_bound": {
            "value": bound, "unit": "line edits", "source": "frontier_costs and remaining line counts",
            "validity": "Every in-band path crosses this row. Every out-of-band path costs at least band+1.",
            "limits": "Lower bound only before a certified terminal cell.",
            "cost": {"frontier_cells_scanned": len(s["frontier"])},
        },
        "threshold_excluded": {
            "value": bound > s["band"], "unit": "boolean", "source": "distance_lower_bound > band",
            "validity": "True certifies absence of a path of cost at most the current threshold.",
            "limits": "A false value at an intermediate row does not establish reachability.",
            "cost": {"comparisons": 1},
        },
        "certified_distance": {
            "value": s["frontier"][-1] if certified else None, "unit": "line edits",
            "source": "terminal frontier cell at the target column",
            "validity": "Defined at the final row only when its target cell costs at most the threshold.",
            "limits": "Costs apply to strict UTF-8 lines with endings retained.",
            "cost": {"terminal_cell_reads": int(s["row"] == s["source_lines"])},
        },
    }
    actions = []
    for name in names:
        description, args = _COMMANDS[name]
        if name == "widen":
            remaining = s["config"]["cell_budget"] - s["total_cells"]
            max_band = max(s["source_lines"], s["target_lines"])
            if remaining <= s["target_lines"]:
                max_band = min(max_band, remaining - 1)
            args = {"band": {"type": "integer", "minimum": s["band"] + 1, "maximum": max_band}}
        actions.append({"name": name, "description": description, "args": dict(args)})
    return {"status": status, "raw": raw, "sensors": sensors, "actions": actions}


def _advance(s, a, b, stage):
    n, m, k = len(a), len(b), s["band"]
    infinity = n + m + 1
    last = min(n, s["row"] + s["config"]["rows_per_step"])
    with (stage / TRACE).open("ab") as trace:
        if trace.tell() != s["attempt_cells"]:
            raise ValueError("trace length disagrees with published state")
        for i in range(s["row"] + 1, last + 1):
            lo, hi = _span(i, m, k)
            width = hi - lo + 1
            if s["total_cells"] + width > s["config"]["cell_budget"]:
                break
            # Padded frontiers permit constant-time lookups at band edges.
            previous_lo, _ = _span(i - 1, m, k)
            previous = [infinity, *s["frontier"], infinity]
            current = [infinity]
            directions = bytearray()
            for j in range(lo, hi + 1):
                p = j - previous_lo + 1
                diagonal = previous[p - 1] + (a[i - 1] != b[j - 1]) if j else infinity
                deletion = previous[p] + 1
                insertion = current[-1] + 1
                cost = min(diagonal, deletion, insertion)
                if cost == diagonal:
                    direction = DIAGONAL
                elif cost == deletion:
                    direction = DELETE
                else:
                    direction = INSERT
                directions.append(direction)
                current.append(cost)
            trace.write(directions)
            s["frontier"] = current[1:]
            s["row"] = i
            s["total_cells"] += width
            s["attempt_cells"] += width
            s["recomputed_cells"] += s["previous_widths"][i]


def _finish(s, a, b, stage):
    trace = (stage / TRACE).read_bytes()
    if len(trace) != s["attempt_cells"]:
        raise ValueError("trace length disagrees with published state")
    i, j, k = len(a), len(b), s["band"]
    offsets = []
    offset = 0
    for row in range(i + 1):
        lo, hi = _span(row, len(b), k)
        offsets.append(offset - lo)
        offset += hi - lo + 1
    operations = []
    while i or j:
        direction = trace[offsets[i] + j]
        if direction == DIAGONAL and i and j:
            old, new = a[i - 1], b[j - 1]
            op = "equal" if old == new else "replace"
            i -= 1
            j -= 1
        elif direction == DELETE and i:
            op, old, new = "delete", a[i - 1], None
            i -= 1
        elif direction == INSERT and j:
            op, old, new = "insert", None, b[j - 1]
            j -= 1
        else:
            raise ValueError("invalid predecessor in trace")
        operations.append({"op": op, "old": old, "new": new})
    operations.reverse()
    reconstructed = "".join(item["new"] for item in operations if item["new"] is not None).encode("utf-8")
    distance = s["frontier"][-1]
    if (hashlib.sha256(reconstructed).hexdigest() != s["target_sha256"]
            or sum(item["op"] != "equal" for item in operations) != distance):
        raise ValueError("traceback inconsistent with certified result")
    patch = {"schema_version": 1, "source_sha256": s["source_sha256"], "target_sha256": s["target_sha256"],
             "distance": distance, "operations": operations}
    (stage / "patch.json").write_bytes(json.dumps(patch, ensure_ascii=False).encode("utf-8"))
    (stage / "reconstructed.txt").write_bytes(reconstructed)
    s["finished"] = True
    return len(operations)


def step(state: dict, action: dict, inputs: dict[str, Path], stage: Path) -> tuple[dict, dict]:
    if type(action) is not dict or set(action) != {"name", "args"} or type(action["args"]) is not dict:
        raise ValueError("expected action name and argument object")
    name, args = action["name"], action["args"]
    _, names = _choices(state, _lower_bound(state))
    if name not in names or set(args) != ({"band"} if name == "widen" else set()):
        raise ValueError("action or arguments unavailable at this checkpoint")
    s = dict(state)
    artifacts = []
    rows = 0
    traceback_operations = 0
    if name in ("widen", "exact"):
        maximum = max(s["source_lines"], s["target_lines"])
        band = maximum if name == "exact" else _integer(args["band"], "band", s["band"] + 1, maximum)
        if s["total_cells"] + _width(0, s["target_lines"], band) > s["config"]["cell_budget"]:
            raise ValueError("remaining cell budget cannot initialize that band")
        _restart(s, stage, band, "exact" if name == "exact" else "banded")
    else:
        source, a = _load(inputs["source"])
        target, b = _load(inputs["target"])
        if (hashlib.sha256(source).hexdigest() != s["source_sha256"]
                or hashlib.sha256(target).hexdigest() != s["target_sha256"]):
            raise ValueError("captured input identity changed")
        s["input_bytes_read"] += len(source) + len(target)
        if name == "continue":
            _advance(s, a, b, stage)
            rows = s["row"] - state["row"]
        else:
            traceback_operations = _finish(s, a, b, stage)
            artifacts = ["patch.json", "reconstructed.txt"]
    effect = {"action": name, "attempt": s["attempt"], "band": s["band"], "mode": s["mode"],
              "source_rows_advanced": rows, "cells_evaluated": s["total_cells"] - state["total_cells"],
              "cells_recomputed": s["recomputed_cells"] - state["recomputed_cells"],
              "cells_discarded": s["discarded_cells"] - state["discarded_cells"],
              "prior_trace_cells_retained": state["attempt_cells"] if name in ("continue", "finish") else 0,
              "traceback_operations": traceback_operations,
              "input_bytes_read": s["input_bytes_read"] - state["input_bytes_read"],
              "artifacts": artifacts}
    return s, effect
