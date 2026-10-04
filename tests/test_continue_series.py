import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import continue_series

MANIFEST = {
    "episodes": [
        {"name": "cal-a", "role": "calibration"},
        {"name": "gate", "role": "gate"},
        {"name": "b1-RG", "role": "scored", "block": "b1"},
        {"name": "b1-Q", "role": "scored", "block": "b1"},
        {"name": "b2-RG", "role": "scored", "block": "b2"},
        {"name": "b2-Q", "role": "scored", "block": "b2"},
    ]
}


class ContinueSeriesTests(unittest.TestCase):
    def check(self, complete, excluded, retained=("b1-RG",)):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in retained:
                (base / name).mkdir()
            with mock.patch.object(continue_series.rs, "completed_episode", lambda out, binding: out.name in complete):
                return continue_series.check_history(MANIFEST, base, "b2", set(excluded), "binding")

    def test_runs_only_the_named_block_past_an_excluded_retained_stop(self):
        block = self.check({"cal-a", "b1-Q"}, {"b1-RG"})
        self.assertEqual([e["name"] for e in block], ["b2-RG", "b2-Q"])

    def test_refuses_an_incomplete_episode_that_was_not_excluded(self):
        with self.assertRaisesRegex(ValueError, "not audited complete"):
            self.check({"cal-a", "b1-Q"}, set())

    def test_refuses_to_exclude_a_completed_episode(self):
        with self.assertRaisesRegex(ValueError, "cannot be excluded"):
            self.check({"cal-a", "b1-Q", "b1-RG"}, {"b1-RG"})

    def test_refuses_to_exclude_an_episode_without_retained_evidence(self):
        with self.assertRaisesRegex(ValueError, "no retained evidence"):
            self.check({"cal-a", "b1-Q"}, {"b1-RG"}, retained=())

    def test_refuses_to_exclude_an_episode_of_the_block_itself_or_later(self):
        with self.assertRaisesRegex(ValueError, "must precede the block"):
            self.check({"cal-a", "b1-Q"}, {"b1-RG", "b2-Q"})


if __name__ == "__main__":
    unittest.main()
