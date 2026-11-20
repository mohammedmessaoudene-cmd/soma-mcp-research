"""Independent acceptance checks for SOMA's local diff and ZIP artifacts."""

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import lzma
from pathlib import Path
import zipfile
import zlib


def _failure(reason: str, **details) -> dict:
    return {"verdict": "FAIL", "reason": reason, **details}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_unique_object)


def _exact_distance(source: list[str], target: list[str]) -> int:
    """Compute the full unit-cost Levenshtein distance with two rows."""
    if len(source) < len(target):
        source, target = target, source
    previous = list(range(len(target) + 1))
    current = [0] * (len(target) + 1)
    for row, old in enumerate(source, start=1):
        current[0] = row
        for column, new in enumerate(target, start=1):
            current[column] = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (old != new),
            )
        previous, current = current, previous
    return previous[-1]


def verify_diff(
    source_path: Path, target_path: Path, patch_path: Path, reconstructed_path: Path
) -> dict:
    """Replay a patch against the source, then check the result and its minimality."""
    try:
        patch = _read_json(patch_path)
        source_bytes = source_path.read_bytes()
        target_bytes = target_path.read_bytes()
        reconstructed_bytes = reconstructed_path.read_bytes()
        source = [line.decode("utf-8") for line in BytesIO(source_bytes)]
        target = [line.decode("utf-8") for line in BytesIO(target_bytes)]
    except (OSError, ValueError, RecursionError) as error:
        return _failure("invalid_diff_input", detail=str(error))

    if (
        not isinstance(patch, dict)
        or type(patch.get("schema_version")) is not int
        or patch["schema_version"] != 1
        or type(patch.get("distance")) is not int
        or patch["distance"] < 0
        or not isinstance(patch.get("operations"), list)
    ):
        return _failure("invalid_patch_schema")

    source_digest = hashlib.sha256(source_bytes).hexdigest()
    target_digest = hashlib.sha256(target_bytes).hexdigest()
    if patch.get("source_sha256") != source_digest:
        return _failure("source_sha256_mismatch", expected=source_digest)
    if patch.get("target_sha256") != target_digest:
        return _failure("target_sha256_mismatch", expected=target_digest)

    source_index = 0
    result = []
    cost = 0
    for index, operation in enumerate(patch["operations"]):
        if not isinstance(operation, dict) or operation.keys() != {"op", "old", "new"}:
            return _failure("invalid_operation_schema", operation_index=index)
        kind, old, new = operation["op"], operation["old"], operation["new"]
        if kind not in ("equal", "replace", "delete", "insert"):
            return _failure("unknown_operation", operation_index=index)
        if kind == "equal" and old != new:
            return _failure("unequal_equal_operation", operation_index=index)

        if kind == "insert":
            if old is not None:
                return _failure("unexpected_old_value", operation_index=index)
        else:
            if source_index == len(source) or old != source[source_index]:
                return _failure("source_line_mismatch", operation_index=index)
            source_index += 1

        if kind == "delete":
            if new is not None:
                return _failure("unexpected_new_value", operation_index=index)
        else:
            target_index = len(result)
            if target_index == len(target) or new != target[target_index]:
                return _failure("target_line_mismatch", operation_index=index)
            result.append(new)

        cost += kind != "equal"

    if source_index != len(source):
        return _failure("unconsumed_source_lines", remaining=len(source) - source_index)
    replayed_bytes = "".join(result).encode("utf-8")
    if replayed_bytes != target_bytes:
        return _failure("replayed_target_mismatch")
    if reconstructed_bytes != replayed_bytes:
        return _failure("reconstructed_file_mismatch")
    if cost != patch["distance"]:
        return _failure("reported_distance_mismatch", reported=patch["distance"], replayed=cost)

    minimum = _exact_distance(source, target)
    if cost != minimum:
        return _failure("non_minimal_patch", replayed=cost, minimum=minimum)
    return {
        "verdict": "PASS",
        "distance": minimum,
        "source_sha256": source_digest,
        "target_sha256": target_digest,
        "operations": len(patch["operations"]),
    }


def verify_archive(zip_path: Path, expected: dict[str, Path]) -> dict:
    """Check member identities, CRCs and bytes against the expected files."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            # orig_filename retains the recorded name before ZipInfo normalizes it.
            names = Counter(member.orig_filename for member in members)
            duplicates = sorted(name for name, count in names.items() if count > 1)
            if names.keys() != expected.keys() or duplicates:
                return _failure(
                    "archive_members_mismatch",
                    missing=sorted(expected.keys() - names.keys()),
                    unexpected=sorted(names.keys() - expected.keys()),
                    duplicates=duplicates,
                )
            for member in members:
                name = member.orig_filename
                if member.is_dir():
                    return _failure("archive_member_is_directory", member=name)
                data = archive.read(member)
                if len(data) != member.file_size:
                    return _failure("archive_member_size_mismatch", member=name)
                if zlib.crc32(data) != member.CRC:
                    return _failure("archive_crc_mismatch", member=name)
                if data != expected[name].read_bytes():
                    return _failure("archive_member_bytes_mismatch", member=name)
    except (
        OSError,
        EOFError,
        ValueError,
        RuntimeError,
        zipfile.BadZipFile,
        zlib.error,
        lzma.LZMAError,
    ) as error:
        return _failure("invalid_archive", detail=str(error))

    return {"verdict": "PASS", "members": sorted(names)}


def _verify_archive_manifest(zip_path: Path, expected_path: Path) -> dict:
    try:
        manifest = _read_json(expected_path)
        if not isinstance(manifest, dict) or any(
            not isinstance(value, str) or not value for value in manifest.values()
        ):
            raise ValueError("expected an object mapping ZIP member names to file paths")
        expected = {name: expected_path.parent / value for name, value in manifest.items()}
    except (OSError, ValueError, RecursionError) as error:
        return _failure("invalid_archive_manifest", detail=str(error))
    return verify_archive(zip_path, expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    diff = subparsers.add_parser("diff", help="verify a diff and its reconstructed file")
    for argument in ("source", "target", "patch", "reconstructed"):
        diff.add_argument(argument, type=Path)
    archive = subparsers.add_parser("archive", help="verify a ZIP against expected files")
    archive.add_argument("zip", type=Path)
    archive.add_argument("expected", type=Path, help="JSON object mapping member names to paths")

    args = parser.parse_args(argv)
    if args.command == "diff":
        result = verify_diff(args.source, args.target, args.patch, args.reconstructed)
    else:
        result = _verify_archive_manifest(args.zip, args.expected)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
