import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sync'))
import limits


class LimitsTests(unittest.TestCase):
    def read(self, value):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'limits.json'
            path.write_text(json.dumps(value))
            return limits.policy(path)

    def test_missing_preserves_old_core_compatibility(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(limits.policy(Path(folder) / 'missing'))
        self.assertIsNone(self.read({'enabled': False}))

    def test_partial_override_inherits_defaults(self):
        result = self.read({'defaults': {'max_tcp': 32}, 'users': {'7': {'max_udp': 4}}})
        self.assertEqual(result['users']['7']['max_tcp'], 32)
        self.assertEqual(result['users']['7']['max_udp'], 4)
        self.assertEqual(result['users']['7']['burst'], 40)

    def test_rejects_bad_values_and_identity(self):
        for data in [{'defaults': {'max_tcp': -1}}, {'defaults': {'max_tcp': True}},
                     {'defaults': {'new_per_second': 0}}, {'users': {'1:7': {}}},
                     {'scope': 'node_user', 'users': {'7': {}}}, {'enabled': 'false'},
                     {'defaults': {'max_tcps': 1}}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.read(data)

    def test_node_scope_and_unlimited(self):
        result = self.read({'scope': 'node_user', 'users': {'1:7': {'new_per_second': 0, 'burst': 0}}})
        self.assertEqual(result['users']['1:7']['new_per_second'], 0)


if __name__ == '__main__':
    unittest.main()
