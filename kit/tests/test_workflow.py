import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import subprocess
import sys
import zipfile

import directory_patch as dp
import workflow


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir=ROOT, prefix=".soma-workflow-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.old, self.new = [self.root / name for name in ("old", "new")]
        for folder in (self.old, self.new):
            folder.mkdir()

    def test_directory_chain_and_independent_replay(self):
        for root, changed in ((self.old, b"a\rb\n"), (self.new, b"x\ry\n")):
            dp.save(root / "nested/text", changed)
            dp.save(root / "same", b"same")
        dp.save(self.old / "removed", b"remove")
        dp.save(self.new / "added", b"add")
        dp.save(self.old / "bytes", b"\xff")
        dp.save(self.new / "bytes", b"\xfb")
        output = self.root / "run"
        report = workflow.run(self.old, self.new, output)
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["diff_oracles"]["nested/text"]["distance"], 1)
        self.assertGreater(report["costs"]["core_work_copy_bytes_each_direction"], 0)
        result = output / "result"
        replay = self.root / "separate"
        dp.replay(self.old, result / "patch", replay)
        for path in (replay, result / "reconstructed", result / "extracted"):
            workflow.verify_tree(path, dp.scan(self.new))
        with zipfile.ZipFile(result / "bundle.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(dp.scan(self.new)))
        manifest = json.loads((result / "patch/manifest.json").read_bytes())
        self.assertEqual({r["op"] for r in manifest["operations"]},
                         {"KEEP", "ADD", "DELETE", "REPLACE", "LINE_PATCH"})
        (self.old / "same").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "precondition"):
            dp.replay(self.old, result / "patch", self.root / "bad")
        self.assertFalse((self.root / "bad").exists())

    def test_empty_target_and_budget(self):
        dp.save(self.old / "last", b"contents")
        report = workflow.run(self.old, self.new, self.root / "empty")
        self.assertEqual(report["archive_methods"], {})
        with patch.object(dp, "MAX_STORAGE", 1024), self.assertRaisesRegex(ValueError, "storage"):
            workflow.run(self.old, self.new, self.root / "blocked")
        self.assertFalse((self.root / "blocked/result").exists())

    def test_masked_stdio_controller_sees_effect_and_both_programs(self):
        dp.save(self.old / "entry", b"a\n")
        dp.save(self.new / "entry", b"b\n")
        received = []
        with subprocess.Popen(
            [sys.executable, "-B", "workflow.py", "run", self.old, self.new, self.root / "run",
             "--controller", "stdio", "--masked"], cwd=ROOT, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as child:
            for line in child.stdout:
                event = json.loads(line)
                if event["type"] == "observation":
                    view = event["observation"]
                    self.assertIsNone(view["sensors"])
                    received.append((view["program_id"], view["status"], view["revision"]))
                if event["type"] == "command_required":
                    action = "exact" if view["program_id"] == "diff" else "store"
                    request = workflow.command(view, action, {}, "Deterministic JSON test client")
                    # This externally chosen ID collided with the next automatic step
                    # before internal commands acquired their reserved namespace.
                    request["action_id"] = f'{view["run_id"]}-{view["revision"] + 2}'
                    child.stdin.write(json.dumps({"scope": "remaining", "request": request}) + "\n")
                    child.stdin.flush()
                if event["type"] == "receipt":
                    self.assertEqual(event["receipt"]["request"]["action_id"], request["action_id"])
            stderr = child.stderr.read()
            self.assertEqual(child.wait(timeout=10), 0, stderr)
        self.assertEqual({p for p, status, revision in received if status == "done" and revision > 1},
                         {"diff", "archive"})
        report = json.loads((self.root / "run/result/report.json").read_bytes())
        self.assertEqual(report["costs"]["external_commands_accepted"], 2)

    def test_stdio_invalid_request_and_reserved_id_return_json_without_effect(self):
        dp.save(self.old / "entry", b"a\n")
        dp.save(self.new / "entry", b"b\n")
        cases = [(None, "request must be a JSON object"), ([], "request must be a JSON object"),
                 ({"action_id": "soma:auto:external"}, "reserved automatic command prefix")]
        for index, (request, reason) in enumerate(cases):
            with self.subTest(request=request):
                dest = self.root / f"refused-{index}"
                result = subprocess.run(
                    [sys.executable, "-B", "workflow.py", "run", self.old, self.new, dest,
                     "--controller", "stdio"], cwd=ROOT, capture_output=True, text=True, timeout=10,
                    input=json.dumps({"scope": "current", "request": request}) + "\n")
                self.assertEqual(result.returncode, 1)
                error = json.loads(result.stderr)
                self.assertEqual(error["type"], "error")
                self.assertIn(reason, error["reason"])
                self.assertEqual(workflow.core.observe(dest / "sessions/diff-000000")["revision"], 0)
                self.assertFalse((dest / "result").exists())

    def test_disallowed_names_and_output_overlap(self):
        for name in ("../escape", "COM1.txt", "dir/CON", "trailing.", "a:b", "A\\B"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dp.safe_name(name)
        with self.assertRaisesRegex(ValueError, "Case collision"):
            dp.check_names(["A/a", "a/b"])
        with self.assertRaisesRegex(ValueError, "disjoint"):
            workflow.run(self.old, self.new, self.new / "out")
