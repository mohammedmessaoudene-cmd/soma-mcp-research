"""Revisioned local sessions for trusted, cooperative program adapters."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter
from copy import deepcopy
from hashlib import file_digest, sha256
from importlib import import_module
from importlib.util import find_spec
import json
import os
from pathlib import Path
from shutil import copy2, copyfile, copytree
from tempfile import TemporaryDirectory
from time import monotonic_ns, sleep
from uuid import uuid4


_STATUSES = {"running", "waiting", "done", "failed", "cancelled"}
_TERMINAL = {"done", "failed", "cancelled"}
_BUILTINS = {"soma_runtime.adapters.diff", "soma_runtime.adapters.archive"}
_METER = ContextVar("soma_cost_meter", default=None)


@contextmanager
def measure_costs():
    """Accumulate logical bytes moved at the specified core call sites."""
    counts = Counter()
    token = _METER.set(counts)
    try:
        yield counts
    finally:
        _METER.reset(token)


def _count(key, value):
    counts = _METER.get()
    if counts is not None:
        counts[key] += value
_REQUEST_KEYS = {
    "run_id", "expected_revision", "observation_id", "masked",
    "action_id", "action", "rationale",
}


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    # Normalize to the same types the next process will read from disk.
    return json.loads(_json_bytes(value))


def _json_id(value):
    return sha256(_json_bytes(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(text):
    value = json.loads(text, object_pairs_hook=_unique_object)
    _json_bytes(value)
    return value


def _read(path):
    data = path.read_bytes()
    _count("core_json_file_bytes_read", len(data))
    return read_json(data.decode("utf-8"))


def _write(path, value):
    data = _json_bytes(value) + b"\n"
    path.write_bytes(data)
    _count("core_json_file_bytes_written", len(data))


def _file_id(path):
    with path.open("rb") as handle:
        digest = file_digest(handle, "sha256").hexdigest()
        _count("core_file_sha256_input_bytes", path.stat().st_size)
        return digest


def _copy_work(source, target):
    copied = copy2(source, target)
    _count("core_work_copy_bytes_each_direction", Path(target).stat().st_size)
    return copied


def _implementation(name):
    if not isinstance(name, str) or not name:
        raise ValueError("Adapter must be an importable module name")
    spec = find_spec(name)
    if spec is None or spec.origin is None or not Path(spec.origin).is_file():
        raise ValueError(f"Adapter must have a local implementation file: {name}")
    result = {
        "scope": "local_module_files", "core_sha256": _file_id(Path(__file__)),
        "adapter_sha256": _file_id(Path(spec.origin)),
    }
    if name in _BUILTINS:
        result["sensors_v1_sha256"] = _file_id(Path(__file__).with_name("sensors_v1.py"))
    return result


def _adapter(name):
    module = import_module(name)
    _object(getattr(module, "INFO", None), "Adapter INFO")
    for method in ("create", "observe", "step"):
        if not callable(getattr(module, method, None)):
            raise ValueError(f"Adapter is missing callable {method}")
    return module


def _verified_adapter(meta):
    name = meta["adapter"]
    if _implementation(name) != meta.get("implementation"):
        raise ValueError("Local implementation file identity differs from the session")
    return _adapter(name)


def _capture_inputs(session, inputs):
    if not isinstance(inputs, dict) or any(not isinstance(key, str) or not key for key in inputs):
        raise ValueError("Inputs must map nonempty names to file paths")
    records = {}
    for index, (name, filename) in enumerate(sorted(inputs.items())):
        source = Path(filename).resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Input is not a regular file: {name}")
        relative = Path("inputs") / f"{index:06d}" / source.name
        target = session / relative
        target.parent.mkdir(parents=True)
        copyfile(source, target)
        _count("core_input_capture_copy_bytes_each_direction", target.stat().st_size)
        records[name] = {"file": relative.as_posix(), "sha256": _file_id(target)}
    return records


def _input_paths(session, records):
    root = session / "inputs"
    paths = {}
    for name, record in records.items():
        path = (session / record["file"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or _file_id(path) != record["sha256"]:
            raise ValueError(f"Captured input changed or disappeared: {name}")
        paths[name] = path
    return paths


def _revision_name(revision):
    return f"r{revision:06d}"


def _revision_paths(session):
    entries = list((session / "revisions").iterdir())
    if any(
        not entry.is_dir() or entry.is_symlink()
        or not entry.name.startswith("r") or not entry.name[1:].isascii()
        or not entry.name[1:].isdigit()
        for entry in entries
    ):
        raise ValueError("Unexpected entry in the published revisions")
    entries.sort(key=lambda entry: int(entry.name[1:]))
    if not entries or any(entry.name != _revision_name(i) for i, entry in enumerate(entries)):
        raise ValueError("Missing or ambiguous published revision")
    return entries


def _store_revision(path, run_id, revision, state, receipt=None):
    _write(path / "state.json", {
        "run_id": run_id, "revision": revision, "state": state,
        "state_id": _json_id(state), "receipt_id": None if receipt is None else _json_id(receipt),
    })
    if receipt is not None:
        _write(path / "receipt.json", receipt)


def _load_revision(path, run_id, revision):
    entry = _read(path / "state.json")
    if (
        entry["run_id"] != run_id or entry["revision"] != revision
        or not (path / "work").is_dir() or not isinstance(entry["state"], dict)
        or _json_id(entry["state"]) != entry["state_id"]
    ):
        raise ValueError(f"Invalid published state at revision {revision}")
    receipt = None
    if revision:
        receipt = _read(path / "receipt.json")
        if (
            receipt["run_id"] != run_id or receipt["revision"] != revision
            or _json_id(receipt) != entry["receipt_id"]
        ):
            raise ValueError(f"Invalid published receipt at revision {revision}")
    elif entry["receipt_id"] is not None or (path / "receipt.json").exists():
        raise ValueError("Unexpected receipt at initial revision")
    return entry["state"], receipt


@contextmanager
def _writer_lock(session):
    # A process exit releases the kernel lock even if no Python cleanup runs.
    with (session / "writer.lock").open("r+b", buffering=0) as lock:
        if os.name == "nt":
            import errno
            import msvcrt

            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    sleep(0.025)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _coordinates(adapter, state):
    if adapter.endswith(".diff"):
        return (state["attempt"], state["source_lines"] - state["row"], "rows",
                state["config"]["cell_budget"], state["total_cells"], "cells")
    return (1, len(state["members"]) - state["index"], "members",
            state["config"]["read_budget"], state["io_read_bytes"], "bytes")


def _common_state(adapter, previous, current, elapsed_ns):
    from . import sensors_v1 as v1
    epoch, rank, unit, limit, spent, budget_unit = _coordinates(adapter, current)
    progress = v1.ProgressSensor(window=2)
    # The measured interval belongs to this single step in this process.
    # Across an attempt boundary there is no preceding rank to compare.
    previous_rank = None
    if previous is not None:
        old_epoch, old_rank, *_ = _coordinates(adapter, previous)
        if old_epoch == epoch:
            previous_rank = old_rank
            progress.update(old_rank, 0, str(old_rank))
    reading = progress.update(rank, elapsed_ns, str(rank))
    return {
        "inputs": {"epoch": epoch, "ranking": rank, "previous_ranking": previous_rank,
                   "ranking_unit": unit, "step_elapsed_ns": elapsed_ns,
                   "limit": limit, "spent": spent, "held": 0, "candidate_ceiling": 0,
                   "budget_unit": budget_unit},
        "derived": {"remaining_budget": v1.budget_margin(limit, spent, 0, 0),
                    "ranking": reading["ranking"], "decrease": reading["decrease"],
                    "progress_per_second": reading["progress_per_second"]},
    }


def _observation(meta, module, revision, state, inputs, workdir, masked):
    view = _object(module.observe(deepcopy(state), dict(inputs), workdir), "Adapter observation")
    if view.keys() != {"status", "raw", "sensors", "actions"} or view["status"] not in _STATUSES:
        raise ValueError("Invalid adapter observation")
    actions = view["actions"]
    if not isinstance(actions, list) or any(
        not isinstance(action, dict) or action.keys() != {"name", "description", "args"}
        or not isinstance(action["name"], str) or not action["name"]
        or not isinstance(action["description"], str)
        for action in actions
    ):
        raise ValueError("Invalid adapter action descriptions")
    if len({action["name"] for action in actions}) != len(actions):
        raise ValueError("Ambiguous adapter action descriptions")
    remaining = meta["max_actions"] - revision
    if remaining < 0:
        raise ValueError("Published revision exceeds session action budget")
    observation = {
        "run_id": meta["run_id"], "revision": revision, "adapter": meta["adapter"],
        "implementation": meta["implementation"],
        "status": view["status"], "raw": view["raw"], "sensors": None if masked else view["sensors"],
        "actions": actions if remaining and view["status"] not in _TERMINAL else [],
        "remaining_actions": remaining, "masked": masked,
        "artifact_root": f"revisions/{_revision_name(revision)}/work",
    }
    if meta["adapter"] in _BUILTINS:
        common = state["_common"]
        raw = observation["raw"]
        observation.update(program_id=meta["adapter"].rsplit(".", 1)[-1],
                           epoch=common["inputs"]["epoch"], state_version=revision,
                           event_seq=revision, source_id=meta["source_id"],
                           phase=raw.get("phase", raw.get("mode")))
        raw["common_inputs"] = common["inputs"]
        if not masked:
            observation["sensors"]["common_v1"] = common["derived"]
    observation["observation_id"] = _json_id(observation)
    if _METER.get() is not None:
        _count("core_observations_computed", 1)
        _count("core_observation_canonical_bytes_computed", len(_json_bytes(observation)))
    return observation


def _request(value):
    request = _object(value, "Request")
    if request.keys() != _REQUEST_KEYS:
        raise ValueError("Invalid request fields")
    for key in ("run_id", "observation_id", "action_id"):
        if not isinstance(request[key], str) or not request[key]:
            raise ValueError(f"{key} must be a nonempty string")
    if type(request["expected_revision"]) is not int or request["expected_revision"] < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if type(request["masked"]) is not bool or not isinstance(request["rationale"], str):
        raise ValueError("masked must be a boolean and rationale a string")
    action = request["action"]
    if (
        not isinstance(action, dict) or action.keys() != {"name", "args"}
        or not isinstance(action["name"], str) or not action["name"]
        or not isinstance(action["args"], dict)
    ):
        raise ValueError("Invalid action: expected a name and an object of args")
    return request


def create_session(session, adapter, inputs, config, max_actions):
    """Capture inputs and publish a complete initial revision."""
    session = Path(session).resolve()
    if session.exists():
        raise FileExistsError(f"Session already exists: {session}")
    if type(max_actions) is not int or max_actions < 1:
        raise ValueError("max_actions must be a positive integer")
    config = _object(config, "Config")
    implementation = _implementation(adapter)
    module = _adapter(adapter)
    with TemporaryDirectory(dir=session.parent, prefix=".soma-init-") as temporary:
        stage = Path(temporary) / "session"
        revision_path = stage / "revisions" / _revision_name(0)
        work = revision_path / "work"
        work.mkdir(parents=True)
        (stage / "writer.lock").touch()
        records = _capture_inputs(stage, inputs)
        meta = {
            "run_id": uuid4().hex, "adapter": adapter, "max_actions": max_actions,
            "inputs": records, "implementation": implementation,
        }
        paths = _input_paths(stage, records)
        state = _object(module.create(config, dict(paths), work), "Initial adapter state")
        if adapter in _BUILTINS:
            meta["source_id"] = _json_id(records)
            state["_common"] = _common_state(adapter, None, state, 0)
        paths = _input_paths(stage, records)
        view = _observation(meta, module, 0, state, paths, work, False)
        _store_revision(revision_path, meta["run_id"], 0, state)
        _write(stage / "session.json", meta)
        if session.exists():
            raise FileExistsError(f"Session already exists: {session}")
        stage.rename(session)
    return view


def observe(session, masked=False):
    """Read the latest completely published revision and project its sensors."""
    if type(masked) is not bool:
        raise ValueError("masked must be a boolean")
    session = Path(session).resolve()
    meta = _read(session / "session.json")
    revisions = _revision_paths(session)
    revision = len(revisions) - 1
    current = revisions[-1]
    state, _ = _load_revision(current, meta["run_id"], revision)
    paths = _input_paths(session, meta["inputs"])
    return _observation(meta, _verified_adapter(meta), revision, state, paths, current / "work", masked)


def _history(revisions, run_id, only_action_id=None):
    receipts, seen = {}, set()
    for revision, path in enumerate(revisions):
        state, receipt = _load_revision(path, run_id, revision)
        if receipt is not None:
            action_id = receipt["request"]["action_id"]
            if action_id in seen:
                raise ValueError(f"Duplicate action_id in published history: {action_id}")
            seen.add(action_id)
            if only_action_id is None or action_id == only_action_id:
                receipts[action_id] = receipt
    return state, receipts


def apply(session, request):
    """Apply one admitted action or return its exact, previously published receipt."""
    session = Path(session).resolve()
    request = _request(request)
    with _writer_lock(session):
        meta = _read(session / "session.json")
        run_id = meta["run_id"]
        if request["run_id"] != run_id:
            raise ValueError("Request run_id does not identify this session")
        revisions = _revision_paths(session)
        state, receipts = _history(revisions, run_id, request["action_id"])
        previous = receipts.get(request["action_id"])
        if previous is not None:
            if _json_bytes(request) != _json_bytes(previous["request"]):
                raise ValueError("action_id already used for a different request")
            return previous
        revision = len(revisions) - 1
        freshness = None
        if meta["adapter"] in _BUILTINS:
            from .sensors_v1 import Observation, freshness_reading
            # Both slots address one immutable published revision. No wall-clock expiry.
            observed = request["expected_revision"]
            freshness = freshness_reading(Observation(observed, observed, 0), revision, revision, 0, 0)
        if request["expected_revision"] != revision or freshness and not freshness["fresh"]:
            raise ValueError("Stale expected_revision")

        module = _verified_adapter(meta)
        paths = _input_paths(session, meta["inputs"])
        masked = request["masked"]
        before = _observation(meta, module, revision, state, paths, revisions[-1] / "work", masked)
        if request["observation_id"] != before["observation_id"]:
            raise ValueError("observation_id does not identify the requested revision and projection")
        if request["action"]["name"] not in {action["name"] for action in before["actions"]}:
            raise ValueError("Action is not available at this observation")

        new_revision = revision + 1
        with TemporaryDirectory(dir=session, prefix=".soma-stage-") as temporary:
            stage = Path(temporary) / "revision"
            work = stage / "work"
            copytree(revisions[-1] / "work", work, copy_function=_copy_work)
            started = monotonic_ns()
            prior = dict(state)
            new_state, effect = module.step(state, deepcopy(request["action"]), dict(paths), work)
            if freshness is not None:
                new_state["_common"] = _common_state(meta["adapter"], prior, new_state,
                                                     max(1, monotonic_ns() - started))
                effect["freshness_v1"] = {"requested_revision": revision, "current_revision": revision,
                                          "clock_scope": "logical_immutable_checkpoint", **freshness}
            new_state = _object(new_state, "Adapter state")
            effect = _object(effect, "Adapter effect")
            paths = _input_paths(session, meta["inputs"])
            receipt = {
                "run_id": run_id, "revision": new_revision, "request": request, "before": before,
                "after": _observation(meta, module, new_revision, new_state, paths, work, masked),
                "effect": effect,
            }
            _store_revision(stage, run_id, new_revision, new_state, receipt)
            stage.rename(session / "revisions" / _revision_name(new_revision))
        return receipt


def journal(session):
    """Return published action receipts in revision order."""
    session = Path(session).resolve()
    meta = _read(session / "session.json")
    _, receipts = _history(_revision_paths(session), meta["run_id"])
    return list(receipts.values())
