"""Functional checks of the diff adapter, independent of the session kernel."""

import hashlib
import itertools
from io import BytesIO
import json
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from soma_runtime.adapters import diff


def _reference(a, b):
    # Enumerate suffix alignment choices backwards from the goal. This oracle
    # has neither a frontier, threshold nor traceback table.
    @lru_cache(None)
    def solve(i, j):
        if i == len(a) or j == len(b):
            return len(a) - i + len(b) - j
        return min(1 + solve(i + 1, j), 1 + solve(i, j + 1),
                   (a[i] != b[j]) + solve(i + 1, j + 1))
    return solve(0, 0)


class DiffTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[1]
        temp = tempfile.TemporaryDirectory(dir=runtime, prefix=".diff-test-")
        self.assertEqual(Path(temp.name).resolve().parent, runtime)
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.inputs = {name: self.work / name for name in ("source", "target")}

    def start(self, source, target, **config):
        self.inputs["source"].write_bytes(source.encode())
        self.inputs["target"].write_bytes(target.encode())
        return diff.create(config, self.inputs, self.work)

    def act(self, state, name, **args):
        before = json.dumps(state, sort_keys=True)
        new, effect = diff.step(state, {"name": name, "args": args}, self.inputs, self.work)
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.assertEqual(new["total_cells"] - state["total_cells"], effect["cells_evaluated"])
        self.assertLessEqual(new["total_cells"], new["config"]["cell_budget"])
        return json.loads(json.dumps(new)), effect

    def view(self, s):
        return diff.observe(s, self.inputs, self.work)

    def check_patch(self, source, target, expected):
        patch = json.loads((self.work / "patch.json").read_bytes())
        self.assertEqual(patch["schema_version"], 1)
        self.assertEqual(patch["source_sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(patch["target_sha256"], hashlib.sha256(target.encode()).hexdigest())
        self.assertEqual(patch["distance"], expected)
        ops = patch["operations"]
        self.assertEqual(sum(op["op"] != "equal" for op in ops), expected)
        remaining = iter(line.decode() for line in BytesIO(source.encode()))
        emitted = []
        for item in ops:
            op, old, new = item["op"], item["old"], item["new"]
            self.assertIn(op, {"equal", "replace", "insert", "delete"})
            if op == "insert":
                self.assertIsNone(old)
            else:
                self.assertEqual(next(remaining), old)
            if op == "delete":
                self.assertIsNone(new)
            else:
                emitted.append(new)
            if op == "equal":
                self.assertEqual(old, new)
            if op == "replace":
                self.assertNotEqual(old, new)
        self.assertEqual(list(remaining), [])
        self.assertEqual("".join(emitted).encode(), target.encode())
        self.assertEqual((self.work / "reconstructed.txt").read_bytes(), target.encode())

    def drive(self, source, target, band, correction):
        a, b = (list(BytesIO(text.encode())) for text in (source, target))
        distance = _reference(a, b)
        s = self.start(source, target, initial_band=band, rows_per_step=1, cell_budget=1500)
        corrected = False
        while True:
            view = self.view(s)
            options = {x["name"] for x in view["actions"]}
            sensors = view["sensors"]
            lower = sensors["distance_lower_bound"]["value"]
            self.assertLessEqual(lower, distance)
            if "finish" in options:
                self.assertEqual(sensors["certified_distance"]["value"], distance)
                s, effect = self.act(s, "finish")
                self.assertCountEqual(effect["artifacts"], ["patch.json", "reconstructed.txt"])
                break
            if "continue" in options:
                s, effect = self.act(s, "continue")
                self.assertEqual(effect["source_rows_advanced"], 1)
            else:
                self.assertTrue(sensors["threshold_excluded"]["value"])
                self.assertGreater(distance, s["band"])
                corrected = True
                args = {"band": s["band"] + 1} if correction == "widen" else {}
                s, _ = self.act(s, correction, **args)
        self.assertEqual(self.view(s)["status"], "done")
        self.assertEqual(corrected, distance > band)
        self.check_patch(source, target, distance)

    def test_all_short_binary_line_sequences(self):
        texts = ["".join(seq) for n in range(4) for seq in itertools.product(("a\n", "b\n"), repeat=n)]
        for source, target in itertools.product(texts, repeat=2):
            for band in range(abs(source.count("\n") - target.count("\n")), max(source.count("\n"), target.count("\n")) + 1):
                with self.subTest(source=source, target=target, band=band):
                    self.drive(source, target, band, "widen" if band % 2 else "exact")

    def test_only_lf_splits_lines_and_final_unterminated_line_survives(self):
        source = "\ufeffx\r\né\nrepeat\rrepeat\u0085\x00last"
        target = "\ufeffx\né\rnew\u2028repeat\u0085last\r\n"
        self.drive(source, target, 1, "exact")

    def test_overlapping_restarts_count_every_recomputed_cell(self):
        s = self.start("a\n" * 8, "a\n" * 8, initial_band=0, rows_per_step=3)
        visited = set()
        repeated = total = 0

        def count(rows, band):
            nonlocal repeated, total
            for i in rows:
                for j in range(9):
                    if abs(i - j) <= band:
                        repeated += (i, j) in visited
                        total += 1
                        visited.add((i, j))

        count([0], 0)
        for name, args in [("continue", {}), ("continue", {}), ("widen", {"band": 1}),
                           ("continue", {}), ("exact", {}), ("continue", {}), ("continue", {})]:
            old = s
            s, effect = self.act(s, name, **args)
            rows = range(old["row"] + 1, s["row"] + 1) if name == "continue" else [0]
            count(rows, s["band"])
            self.assertEqual(s["total_cells"], total)
            self.assertEqual(s["recomputed_cells"], repeated)
            self.assertEqual(s["attempt_cells"], (self.work / "trace.bin").stat().st_size)
            self.assertEqual(s["discarded_cells"] + s["attempt_cells"], total)
            if name != "continue":
                self.assertEqual(effect["cells_discarded"], old["attempt_cells"])

    def test_budget_and_validation(self):
        s = self.start("a\nb\n", "a\nb\n", initial_band=2, cell_budget=6)
        s, effect = self.act(s, "continue")
        self.assertEqual(effect["cells_evaluated"], 3)
        self.assertEqual(self.view(s)["status"], "failed")
        self.assertEqual(self.view(s)["actions"], [])
        with self.assertRaises(ValueError):
            self.act(s, "finish")
        for cfg in ({"initial_band": True}, {"rows_per_step": 0}, {"rows_per_step": 257},
                    {"cell_budget": False}, {"initial_band": -1}, {"unknown": 0}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                self.start("x", "y", **cfg)
        s = self.start("a\nb\n", "x\ny\n", initial_band=0)
        for name, args in (("continue", {"unexpected": 1}), ("widen", {"band": True}),
                           ("widen", {"band": 0}), ("widen", {"band": 3}), ("finish", {})):
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                self.act(s, name, **args)
        self.inputs["source"].write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            diff.create({}, self.inputs, self.work)


if __name__ == "__main__":
    unittest.main()
