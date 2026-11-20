"""Exercise the runtime boundary with a small file-producing test adapter."""

import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from soma_runtime.core import apply, create_session, journal, observe as observe_session


INFO = {"inputs": ["source"], "objective": "Append bytes and count published effects", "actions": ["append"]}
ROOT = Path(__file__).resolve().parents[1]
ADAPTER = "tests.test_core"


def create(config, inputs, stage):
    (stage / "result.txt").write_bytes(inputs["source"].read_bytes())
    return {"calls": 0}


def observe(state, inputs, workdir):
    return {
        "status": "running",
        "raw": {"calls": state["calls"], "size": (workdir / "result.txt").stat().st_size},
        "sensors": {"length": {"value": (workdir / "result.txt").stat().st_size, "unit": "bytes"}},
        "actions": [{"name": "append", "description": "Append data", "args": {"text": "string"}}],
    }


def step(state, action, inputs, stage):
    if action["args"].keys() != {"text"} or not isinstance(action["args"]["text"], str):
        raise ValueError("Append expects only a text argument")
    data = action["args"]["text"].encode("utf-8")
    with (stage / "result.txt").open("ab") as handle:
        handle.write(data)
    if os.environ.get("SOMA_TEST_PROCESS_EXIT_BEFORE_COMMIT") == "yes":
        os._exit(23)
    if os.environ.get("SOMA_TEST_PROCESS_PAUSE_IN_ADAPTER") == "yes":
        (stage / "entered").touch()
        deadline = monotonic() + 10
        while not (stage / "release").exists():
            if monotonic() >= deadline:
                raise TimeoutError("Test did not release its writer")
            sleep(0.025)
        (stage / "entered").unlink()
        (stage / "release").unlink()
    return {"calls": state["calls"] + 1}, {"appended": len(data), "artifacts": ["result.txt"]}


class CoreTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=ROOT, prefix=".soma-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "entry.txt"
        self.source.write_bytes(b"source\n")
        self.session = self.root / "session"
        self.initial = create_session(self.session, ADAPTER, {"source": self.source}, {}, 3)

    def request(self, observation=None, action_id="append-1", text="effect\n"):
        view = observation or self.initial
        return {
            "run_id": view["run_id"], "expected_revision": view["revision"],
            "observation_id": view["observation_id"], "masked": view["masked"],
            "action_id": action_id, "action": {"name": "append", "args": {"text": text}},
            "rationale": "Control the file under test.",
        }

    def artifact(self, observation):
        return (self.session / observation["artifact_root"] / "result.txt").read_bytes()

    def save_request(self, name, request):
        path = self.root / name
        path.write_text(json.dumps(request), encoding="utf-8")
        return path

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "soma_runtime", *(str(arg) for arg in args)],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
        )

    def test_projection_and_revision_checks_precede_effects(self):
        visible = observe_session(self.session)
        hidden = observe_session(self.session, masked=True)
        self.assertEqual(visible, self.initial)
        self.assertEqual(hidden, visible | {
            "sensors": None, "masked": True, "observation_id": hidden["observation_id"],
        })
        self.assertNotEqual(visible["observation_id"], hidden["observation_id"])
        with self.assertRaisesRegex(ValueError, "run_id"):
            apply(self.session, self.request() | {"run_id": "another"})
        with self.assertRaisesRegex(ValueError, "observation_id"):
            apply(self.session, self.request() | {"masked": True})
        with self.assertRaisesRegex(ValueError, "Action is not available"):
            apply(self.session, self.request() | {"action": {"name": "other", "args": {}}})
        with self.assertRaisesRegex(ValueError, "Append expects"):
            apply(self.session, self.request() | {"action": {"name": "append", "args": {"text": True}}})
        self.assertEqual(journal(self.session), [])
        receipt = apply(self.session, self.request(hidden))
        self.assertEqual(receipt["before"], hidden)
        self.assertEqual(receipt["after"], observe_session(self.session, masked=True))
        self.assertEqual(receipt["effect"]["appended"], 7)
        self.assertEqual(self.artifact(visible), b"source\n")
        self.assertEqual(self.artifact(receipt["after"]), b"source\neffect\n")
        with self.assertRaisesRegex(ValueError, "Stale"):
            apply(self.session, self.request(action_id="too-late"))

    def test_occurrence_identity_and_budget(self):
        original = self.request()
        first = apply(self.session, original)
        self.assertEqual(apply(self.session, original), first)
        with self.assertRaisesRegex(ValueError, "different request"):
            apply(self.session, original | {"rationale": "Changed"})
        second = apply(self.session, self.request(first["after"], action_id="append-2"))
        self.assertEqual(second["request"]["action"], first["request"]["action"])
        self.assertEqual(self.artifact(second["after"]), b"source\neffect\neffect\n")
        third = apply(self.session, self.request(second["after"], action_id="append-3"))
        self.assertEqual(third["after"]["remaining_actions"], 0)
        self.assertEqual(third["after"]["actions"], [])
        self.assertEqual(apply(self.session, original), first)
        with self.assertRaisesRegex(ValueError, "Action is not available"):
            apply(self.session, self.request(third["after"], action_id="append-4"))
        self.assertEqual(journal(self.session), [first, second, third])

    def test_original_is_isolated_and_changed_capture_is_detected(self):
        self.source.write_bytes(b"changed original")
        self.assertEqual(observe_session(self.session), self.initial)
        first = apply(self.session, self.request())
        captured, = (self.session / "inputs").glob("*/*")
        captured.write_bytes(b"changed capture")
        with self.assertRaisesRegex(ValueError, "Captured input changed"):
            observe_session(self.session)
        with self.assertRaisesRegex(ValueError, "Captured input changed"):
            apply(self.session, self.request(first["after"], action_id="new"))
        self.assertEqual(apply(self.session, self.request()), first)
        self.assertEqual(journal(self.session), [first])

    def test_adapter_file_change_stops_new_work_and_preserves_historical_receipt(self):
        adapter = "soma_test_local_adapter"
        source = self.root / f"{adapter}.py"
        source.write_text("from tests.test_core import INFO, create, observe, step\n", encoding="utf-8")
        session = self.root / "versioned"
        self.addCleanup(sys.modules.pop, adapter, None)
        with patch.object(sys, "path", [str(self.root), *sys.path]):
            first = create_session(session, adapter, {"source": self.source}, {}, 3)
            expected_identity = {
                "scope": "local_module_files",
                "core_sha256": sha256((ROOT / "soma_runtime" / "core.py").read_bytes()).hexdigest(),
                "adapter_sha256": sha256(source.read_bytes()).hexdigest(),
            }
            self.assertEqual(first["implementation"], expected_identity)
            self.assertEqual(observe_session(session, masked=True)["implementation"], expected_identity)
            request = self.request(first)
            published = apply(session, request)
            source.write_text(
                "raise RuntimeError('Modified adapter must not be loaded')\n", encoding="utf-8"
            )
            # Remove the import cache so verification must reject even before importing the new source.
            sys.modules.pop(adapter)
            with self.assertRaisesRegex(ValueError, "implementation file identity"):
                observe_session(session)
            with self.assertRaisesRegex(ValueError, "implementation file identity"):
                apply(session, self.request(published["after"], action_id="new"))
            self.assertEqual(apply(session, request), published)
            self.assertEqual(journal(session), [published])
            self.assertNotIn(adapter, sys.modules)

    def test_process_exit_before_publication_and_retry(self):
        request = self.request()
        path = self.save_request("before-exit.json", request)
        child = subprocess.run(
            [sys.executable, "-B", "-m", "soma_runtime", "apply", str(self.session), "--request", str(path)],
            cwd=ROOT, capture_output=True, timeout=15,
            env=os.environ | {"SOMA_TEST_PROCESS_EXIT_BEFORE_COMMIT": "yes"},
        )
        self.assertEqual(child.returncode, 23, child.stderr)
        self.assertEqual(child.stdout, b"")
        self.assertEqual(observe_session(self.session), self.initial)
        self.assertEqual(journal(self.session), [])
        receipt = apply(self.session, request)
        self.assertEqual(receipt["revision"], 1)
        self.assertEqual(self.artifact(receipt["after"]), b"source\neffect\n")

    def test_process_exit_after_publication_before_response(self):
        request = self.request()
        path = self.save_request("after-exit.json", request)
        code = (
            "from pathlib import Path\n"
            "from soma_runtime.core import apply, read_json\n"
            "import os, sys\n"
            "apply(sys.argv[1], read_json(Path(sys.argv[2]).read_text(encoding='utf-8')))\n"
            "os._exit(24)\n"
        )
        child = subprocess.run(
            [sys.executable, "-B", "-c", code, str(self.session), str(path)],
            cwd=ROOT, capture_output=True, timeout=15,
        )
        self.assertEqual(child.returncode, 24, child.stderr)
        self.assertEqual(child.stdout, b"")
        self.assertEqual(observe_session(self.session)["revision"], 1)
        published, = journal(self.session)
        self.assertEqual(apply(self.session, request), published)
        self.assertEqual(self.artifact(published["after"]), b"source\neffect\n")

    def test_concurrent_writers_and_reader_at_publication_boundary(self):
        def writer(path, env):
            return subprocess.Popen(
                [sys.executable, "-B", "-m", "soma_runtime", "apply", str(self.session), "--request", str(path)],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )

        path_a = self.save_request("a.json", self.request(action_id="first"))
        path_b = self.save_request("b.json", self.request(action_id="competing"))
        with writer(path_a, os.environ | {"SOMA_TEST_PROCESS_PAUSE_IN_ADAPTER": "yes"}) as first:
            deadline = monotonic() + 5
            while not (markers := list(self.session.glob(".soma-stage-*/revision/work/entered"))):
                if first.poll() is not None or monotonic() >= deadline:
                    self.fail("First writer did not reach the staging barrier")
                sleep(0.025)
            with (self.session / "writer.lock").open("r+b", buffering=0) as lock:
                if os.name == "nt":
                    import msvcrt

                    with self.assertRaises(OSError) as denied:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    self.assertIn(denied.exception.errno, {errno.EACCES, errno.EAGAIN, errno.EDEADLK})
                else:
                    import fcntl

                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with writer(path_b, os.environ) as second:
                self.assertEqual(observe_session(self.session), self.initial)
                self.assertEqual(journal(self.session), [])
                self.assertEqual(self.artifact(self.initial), b"source\n")
                markers[0].with_name("release").touch()
                first_out, first_err = first.communicate(timeout=15)
                second_out, second_err = second.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, first_err)
        self.assertNotEqual(second.returncode, 0, second_err)
        self.assertIn("Stale", json.loads(second_out)["error"])
        receipt = json.loads(first_out)
        self.assertEqual(journal(self.session), [receipt])
        self.assertEqual(self.artifact(receipt["after"]), b"source\neffect\n")

    def test_durable_state_or_receipt_corruption_does_not_fall_back(self):
        receipt = apply(self.session, self.request())
        folder = (self.session / receipt["after"]["artifact_root"]).parent
        for filename, key, replacement in (
            ("state.json", "state", {"calls": 99}),
            ("receipt.json", "effect", {"appended": 99}),
        ):
            with self.subTest(filename=filename):
                path = folder / filename
                original = path.read_bytes()
                altered = json.loads(original) | {key: replacement}
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid published"):
                    observe_session(self.session)
                with self.assertRaisesRegex(ValueError, "Invalid published"):
                    journal(self.session)
                path.write_bytes(original)

    def test_cli_read_write_and_json_errors(self):
        session = self.root / "via-cli"
        inputs = self.root / "inputs.json"
        inputs.write_text(json.dumps({"source": str(self.source)}), encoding="utf-8-sig")
        result = self.cli(
            "init", session, "--adapter", ADAPTER, "--inputs", inputs, "--config", "{}", "--max-actions", 1
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        initial = json.loads(result.stdout)
        result = self.cli("observe", session, "--masked")
        self.assertEqual(result.returncode, 0, result.stdout)
        hidden = json.loads(result.stdout)
        self.assertIsNone(hidden["sensors"])
        path = self.save_request("request.json", self.request(hidden))
        result = self.cli("apply", session, "--request", f"@{path}")
        self.assertEqual(result.returncode, 0, result.stdout)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["before"]["run_id"], initial["run_id"])
        self.assertEqual(json.loads(self.cli("journal", session).stdout), [receipt])
        duplicate = self.cli("apply", session, "--request", '{"action_id":"a","action_id":"b"}')
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("Duplicate JSON key", json.loads(duplicate.stdout)["error"])
        with self.assertRaises(ValueError):
            apply(self.session, self.request() | {"rationale": float("nan")})
        with self.assertRaisesRegex(ValueError, "positive integer"):
            create_session(self.root / "invalid", ADAPTER, {"source": self.source}, {}, True)


if __name__ == "__main__":
    unittest.main()
