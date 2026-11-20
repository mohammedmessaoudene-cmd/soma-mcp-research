import copy
from pathlib import Path
import tempfile
import unittest
import zipfile

from soma_runtime.adapters import archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temp_base = Path(__file__).resolve().parents[1] / '.test-tmp'
        temp_base.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=temp_base)
        if not Path(self.tmp.name).resolve().is_relative_to(temp_base.resolve()):
            raise RuntimeError('Test directory escaped workspace')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.inputs = {}
        for name, data in {'a': ('épreuve\r\n' * 300).encode(), 'b': bytes(range(256)) * 8, 'c': b''}.items():
            path = self.root / name
            path.write_bytes(data)
            self.inputs[name] = path

    def initial(self, **extra):
        return archive.create({'members': {'a': 'text.txt', 'b': 'raw.bin', 'c': 'empty'}, **extra},
                              self.inputs, self.work)

    def apply(self, state, name):
        return archive.step(copy.deepcopy(state), {'name': name, 'args': {}}, self.inputs, self.work)

    def test_actual_probe_then_mixed_methods_exact_artifact(self):
        state = self.initial(probe_bytes=1024)
        total_reads = 0
        for i, method in enumerate(['compress', 'store', 'store']):
            state, probe_effect = self.apply(state, 'probe')
            total_reads += probe_effect['read_bytes']
            view = archive.observe(state, self.inputs, self.work)
            self.assertEqual(view['status'], 'waiting')
            self.assertEqual({a['name'] for a in view['actions']}, {'store', 'compress'})
            if i == 1:
                self.assertEqual(view['sensors']['prefix_entropy']['value'], 8)
            if i == 2:
                self.assertFalse(view['sensors']['prefix_compression_gain']['valid'])
                self.assertIsNone(view['sensors']['prefix_compression_gain']['value'])
            state, effect = self.apply(state, method)
            total_reads += effect['read_bytes']
        state, effect = self.apply(state, 'finish')
        self.assertEqual(state['status'], 'done')
        self.assertEqual(state['io_read_bytes'], total_reads)
        with zipfile.ZipFile(self.work / 'bundle.zip') as z:
            self.assertEqual(z.namelist(), ['text.txt', 'raw.bin', 'empty'])
            self.assertIsNone(z.testzip())
            for key, name in [('a', 'text.txt'), ('b', 'raw.bin'), ('c', 'empty')]:
                self.assertEqual(z.read(name), self.inputs[key].read_bytes())
            self.assertEqual(z.getinfo('text.txt').compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(z.getinfo('raw.bin').compress_type, zipfile.ZIP_STORED)
        self.assertEqual(effect['artifact_hash_read_bytes'], (self.work / 'bundle.zip').stat().st_size)

    def test_budget_failure_precedes_read_or_member_write(self):
        state = self.initial(read_budget=1, probe_bytes=2)
        previous = (self.work / 'bundle.zip').read_bytes()
        state, effect = self.apply(state, 'probe')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['io_read_bytes'], 0)
        self.assertEqual(effect['read_bytes'], 0)
        self.assertEqual((self.work / 'bundle.zip').read_bytes(), previous)

    def test_guarded_commands_and_configuration(self):
        for bad in [True, 0, -1, 1048577]:
            with self.assertRaises(ValueError):
                self.initial(probe_bytes=bad)
        for names in [{'a': '../bad', 'b': 'b', 'c': 'c'}, {'a': 'same', 'b': 'same', 'c': 'c'},
                      {'a': 'a\x00hidden', 'b': 'b', 'c': 'c'}]:
            with self.assertRaises(ValueError):
                archive.create({'members': names}, self.inputs, self.work)
        state = self.initial()
        with self.assertRaises(ValueError):
            self.apply(state, 'store')
        with self.assertRaises(ValueError):
            archive.step(state, {'name': 'probe', 'args': {'unexpected': 1}}, self.inputs, self.work)


if __name__ == '__main__':
    unittest.main()
