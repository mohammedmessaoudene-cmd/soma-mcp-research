"""Portable file-tree patch. Paths are relative to dedicated, cooperative trees."""
from hashlib import sha256
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path, PureWindowsPath
import stat
from tempfile import TemporaryDirectory

from soma_runtime.core import read_json

MIB = 1024 * 1024
MAX_FILES = 256
MAX_INPUT = 16 * MIB
MAX_FILE = MIB
MAX_LINES = 2048
MAX_CELLS = 20_000_000
MAX_STORAGE = 256 * MIB
_METER = ContextVar("directory_costs", default=None)


@contextmanager
def measure(counts):
    token = _METER.set(counts)
    try:
        yield
    finally:
        _METER.reset(token)


def count(key, amount):
    counts = _METER.get()
    if counts is not None:
        counts[key] += amount


def safe_name(name):
    if not isinstance(name, str) or len(name.encode("utf-8")) > 240:
        raise ValueError("Path must be a relative UTF-8 name of at most 240 bytes")
    for part in name.split("/"):
        if (part in {"", ".", ".."} or part.endswith((" ", "."))
                or any(ord(c) < 32 or c in '<>:"|?*\\' for c in part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"Unsupported Windows path component: {part!r}")
    return name


def plain(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
        raise ValueError(f"Symlink/reparse point: {path}")
    return info


def absolute(path):
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Use a normalized path without '..'")
    path = path.absolute()
    for ancestor in (*reversed(path.parents), path):
        if os.path.lexists(ancestor):
            plain(ancestor)
    return path


def distinct(*paths):
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError("Input, patch and output trees must be disjoint")


def check_names(names):
    seen = {}
    for name in names:
        safe_name(name)
        parts = name.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            prior = seen.setdefault(prefix.casefold(), prefix)
            if prior != prefix:
                raise ValueError(f"Case collision: {prior!r} and {prefix!r}")


def scan(root, *, max_files=MAX_FILES, max_file=MAX_FILE, max_total=MAX_INPUT):
    if not stat.S_ISDIR(plain(root).st_mode):
        raise ValueError(f"Expected directory: {root}")
    files, names = {}, []
    total = 0
    pending = [root]
    while pending:
        for entry in pending.pop().iterdir():
            info = plain(entry)
            name = safe_name(entry.relative_to(root).as_posix())
            names.append(name)
            if len(names) > 4096:
                raise ValueError("Too many directory entries")
            if stat.S_ISDIR(info.st_mode):
                pending.append(entry)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Unsupported file type: {name}")
            total += info.st_size
            if info.st_size > max_file or total > max_total or len(files) >= max_files:
                raise ValueError("Directory size or file count exceeds profile")
            files[name] = entry
    check_names(names)
    return files


def data_at(path, limit):
    if path.stat().st_size > limit:
        raise ValueError(f"File size exceeds limit: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File grew beyond limit: {path}")
    count("directory_helper_bytes_read", len(data))
    return data


def identity(data):
    count("directory_identity_hash_input_bytes", len(data))
    return {"bytes": len(data), "sha256": sha256(data).hexdigest()}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    count("directory_helper_bytes_written", len(data))


def save_json(path, value):
    save(path, json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode() + b"\n")


def load_json(data):
    return read_json(data.decode("utf-8"))


def line_count(data):
    return data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))


def fallback(data):
    if b"\0" in data:
        return "NUL"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "INVALID_UTF8"
    if line_count(data) > MAX_LINES:
        return "LINE_LIMIT"
    return None


def apply_lines(source, script):
    tokens = source.split(b"\n")
    lines = [part + b"\n" for part in tokens[:-1]]
    if tokens[-1]:
        lines.append(tokens[-1])
    pos = changes = 0
    output = bytearray()
    if script["schema_version"] != 1 or sha256(source).hexdigest() != script["source_sha256"]:
        raise ValueError("Line patch source precondition failed")
    for op in script["operations"]:
        kind, old, new = op["op"], op["old"], op["new"]
        if kind not in {"equal", "replace", "insert", "delete"}:
            raise ValueError("Unknown line operation")
        if kind == "insert":
            if old is not None:
                raise ValueError("Invalid insert")
        else:
            if pos >= len(lines) or not isinstance(old, str) or old.encode() != lines[pos]:
                raise ValueError("Line patch source mismatch")
            pos += 1
        if kind == "delete":
            if new is not None:
                raise ValueError("Invalid deletion")
        else:
            if not isinstance(new, str) or kind == "equal" and old != new:
                raise ValueError("Invalid line payload")
            output.extend(new.encode())
            if len(output) > MAX_FILE:
                raise ValueError("Reconstructed file exceeds limit")
        changes += kind != "equal"
    if (pos != len(lines) or changes != script["distance"]
            or sha256(output).hexdigest() != script["target_sha256"]):
        raise ValueError("Line patch result mismatch")
    return bytes(output)


def replay(old, patch_dir, destination):
    old, patch_dir, destination = map(absolute, (old, patch_dir, destination))
    distinct(old, patch_dir, destination)
    if os.path.lexists(destination):
        raise FileExistsError(destination)
    sources = scan(old)
    objects = scan(patch_dir, max_files=MAX_FILES + 1, max_file=32 * MIB,
                   max_total=MAX_STORAGE)
    spec = load_json(data_at(objects.pop("manifest.json"), 2 * MIB))
    records = spec["operations"]
    if spec["schema"] != "soma.directory-patch.v1" or len(records) > MAX_FILES:
        raise ValueError("Unknown patch schema or too many operations")
    names = [record["path"] for record in records]
    check_names(names)
    if len(set(names)) != len(names):
        raise ValueError("Duplicate patch path")
    old_names = {r["path"] for r in records if r["source"] is not None}
    if old_names != set(sources):
        raise ValueError("Patch source file set mismatch")
    # Verify all preconditions and payloads before creating the new directory.
    results, target_bytes = {}, 0
    for record in records:
        name, kind = record["path"], record["op"]
        data = data_at(sources[name], MAX_FILE) if name in sources else None
        if (None if data is None else identity(data)) != record["source"]:
            raise ValueError(f"Source precondition failed: {name}")
        if kind == "DELETE" and data is not None and record["target"] is None:
            continue
        if kind == "KEEP" and data is not None:
            result = data
        elif (kind == "ADD" and data is None) or (kind == "REPLACE" and data is not None):
            result = data_at(objects.pop(record["payload"]), MAX_FILE)
        elif kind == "LINE_PATCH" and data is not None:
            script = load_json(data_at(objects.pop(record["payload"]), 32 * MIB))
            result = apply_lines(data, script)
        else:
            raise ValueError(f"Invalid directory patch operation: {kind}")
        if identity(result) != record["target"]:
            raise ValueError(f"Target identity mismatch: {name}")
        target_bytes += len(result)
        if target_bytes > MAX_INPUT:
            raise ValueError("Target exceeds directory profile")
        results[name] = result
    if objects:
        raise ValueError("Unexpected patch payloads")
    for name in results:
        if any(parent.as_posix() in results for parent in Path(name).parents):
            raise ValueError("File and directory path conflict")
    with TemporaryDirectory(dir=destination.parent, prefix=".soma-replay-") as temporary:
        stage = Path(temporary) / "tree"
        stage.mkdir()
        for name, value in results.items():
            save(stage / name, value)
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        stage.rename(destination)
    return {"verified_files": len(results), "bytes": target_bytes}
