import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile

from verification.oracles import main, verify_archive, verify_diff


class OracleTestCase(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1]
        scratch = (runtime / ".test-tmp").resolve()
        if not scratch.is_relative_to(runtime):
            raise RuntimeError("Temporary test directory escaped the runtime")
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)


class DiffOracleTests(OracleTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.folder / "source.txt"
        self.target = self.folder / "target.txt"
        self.patch = self.folder / "patch.json"
        self.reconstruction = self.folder / "reconstructed.txt"

    def write_patch(self, source, target, operations, distance):
        self.source.write_bytes(source)
        self.target.write_bytes(target)
        self.reconstruction.write_bytes(target)
        self.patch.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_sha256": sha256(source).hexdigest(),
                    "target_sha256": sha256(target).hexdigest(),
                    "distance": distance,
                    "operations": [
                        {"op": kind, "old": old, "new": new}
                        for kind, old, new in operations
                    ],
                }
            ),
            encoding="utf-8",
        )

    def verify(self):
        return verify_diff(self.source, self.target, self.patch, self.reconstruction)

    def test_valid_patch_preserves_utf8_line_endings_and_final_line(self):
        self.write_patch(
            "gardé\r\navant\r\nsuppression\nfin\n".encode(),
            "gardé\r\naprès\r\nfin\najout".encode(),
            [
                ("equal", "gardé\r\n", "gardé\r\n"),
                ("replace", "avant\r\n", "après\r\n"),
                ("delete", "suppression\n", None),
                ("equal", "fin\n", "fin\n"),
                ("insert", None, "ajout"),
            ],
            3,
        )
        result = self.verify()
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["distance"], 3)

    def test_empty_source_and_target(self):
        self.write_patch(b"", b"", [], 0)
        self.assertEqual(self.verify()["verdict"], "PASS")

    def test_only_lf_delimits_a_line(self):
        old = "A\rB\u2028C\u0085D\r\n"
        new = "E\rF\u2028G\u0085H\r\n"
        self.write_patch(
            (old + "\nZ").encode("utf-8"),
            (new + "\nZ").encode("utf-8"),
            [("replace", old, new), ("equal", "\n", "\n"), ("equal", "Z", "Z")],
            1,
        )
        result = self.verify()
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["distance"], 1)

    def test_unicode_separator_does_not_authorize_split_operations(self):
        self.write_patch(
            "a\u2028b\n".encode("utf-8"),
            "a\u2028c\n".encode("utf-8"),
            [("equal", "a\u2028", "a\u2028"), ("replace", "b\n", "c\n")],
            1,
        )
        result = self.verify()
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "source_line_mismatch")

    def test_claimed_hash_does_not_validate_false_patch(self):
        for old, new, reason in (
            ("elsewhere\n", "right\n", "source_line_mismatch"),
            ("left\n", "wrong\n", "target_line_mismatch"),
        ):
            with self.subTest(reason=reason):
                self.write_patch(b"left\n", b"right\n", [("replace", old, new)], 1)
                result = self.verify()
                self.assertEqual(result["verdict"], "FAIL", result)
                self.assertEqual(result["reason"], reason)

    def test_one_operation_cannot_reconstruct_multiple_target_lines(self):
        self.write_patch(b"a\n", b"b\nc\n", [("replace", "a\n", "b\nc\n")], 1)
        result = self.verify()
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "target_line_mismatch")

    def test_reconstructing_patch_can_still_be_nonminimal(self):
        self.write_patch(
            b"left\n",
            b"right\n",
            [("delete", "left\n", None), ("insert", None, "right\n")],
            2,
        )
        result = self.verify()
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "non_minimal_patch")
        self.assertEqual(result["minimum"], 1)

    def test_false_distance_is_rejected(self):
        self.write_patch(b"a", b"b", [("replace", "a", "b")], 0)
        result = self.verify()
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "reported_distance_mismatch")

    def test_corrupted_inputs_and_published_reconstruction(self):
        for path, reason in (
            (self.source, "source_sha256_mismatch"),
            (self.target, "target_sha256_mismatch"),
            (self.reconstruction, "reconstructed_file_mismatch"),
        ):
            with self.subTest(reason=reason):
                self.write_patch(b"left\r\n", b"right\r\n", [("replace", "left\r\n", "right\r\n")], 1)
                path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
                result = self.verify()
                self.assertEqual(result["verdict"], "FAIL", result)
                self.assertEqual(result["reason"], reason)

    def test_diff_cli_outputs_structured_pass(self):
        self.write_patch(b"a", b"b", [("replace", "a", "b")], 1)
        output = io.StringIO()
        paths = [self.source, self.target, self.patch, self.reconstruction]
        with contextlib.redirect_stdout(output):
            status = main(["diff", *map(str, paths)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "PASS")


class ArchiveOracleTests(OracleTestCase):
    def setUp(self):
        super().setUp()
        self.archive = self.folder / "bundle.zip"
        self.contents = {"résumé.txt": b"\xc3\xa9\r\n", "data/payload.bin": b"\x00\xff"}
        self.expected = {}
        for index, (name, data) in enumerate(self.contents.items()):
            source = self.folder / f"file{index}"
            source.write_bytes(data)
            self.expected[name] = source

    def write_archive(self, members):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for index, (name, data) in enumerate(members):
                compression = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)[index % 2]
                archive.writestr(name, data, compress_type=compression)

    def test_accepts_correct_members_independent_of_order_and_compression(self):
        self.write_archive(reversed(list(self.contents.items())))
        result = verify_archive(self.archive, self.expected)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["members"], sorted(self.expected))

    def test_rejects_missing_unexpected_or_duplicated_member(self):
        first, second = self.contents.items()
        for members, field, name in (
            ([first], "missing", second[0]),
            ([first, second, ("extra", b"data")], "unexpected", "extra"),
            ([first, second, first], "duplicates", first[0]),
        ):
            with self.subTest(field=field):
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                    self.write_archive(members)
                result = verify_archive(self.archive, self.expected)
                self.assertEqual(result["verdict"], "FAIL", result)
                self.assertEqual(result["reason"], "archive_members_mismatch")
                self.assertEqual(result[field], [name])

    def test_valid_crc_does_not_make_wrong_member_bytes_acceptable(self):
        self.write_archive({**self.contents, "data/payload.bin": b"\xff\x00"}.items())
        result = verify_archive(self.archive, self.expected)
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "archive_member_bytes_mismatch")
        self.assertEqual(result["member"], "data/payload.bin")

    def test_rejects_corrupted_zip_member(self):
        payload = b"original member bytes"
        self.write_archive([("one", payload)])
        source = self.folder / "source"
        replacement = b"modified member bytes"
        source.write_bytes(replacement)
        blob = self.archive.read_bytes()
        self.assertEqual(blob.count(payload), 1)
        self.assertEqual(len(payload), len(replacement))
        self.archive.write_bytes(blob.replace(payload, replacement))

        result = verify_archive(self.archive, {"one": source})
        self.assertEqual(result["verdict"], "FAIL", result)
        self.assertEqual(result["reason"], "invalid_archive")
        self.assertIn("CRC", result["detail"])

    def test_archive_cli_resolves_manifest_paths_and_returns_fail(self):
        self.write_archive(self.contents.items())
        manifest = self.folder / "expected.json"
        manifest.write_text(
            json.dumps({name: path.name for name, path in self.expected.items()}),
            encoding="utf-8",
        )
        arguments = ["archive", str(self.archive), str(manifest)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "PASS")

        self.expected["data/payload.bin"].write_bytes(b"changed")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(arguments)
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
