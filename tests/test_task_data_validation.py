import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLLING = [
    "--task",
    "rolling",
    "--cycles",
    "36",
    "--submission-cycle",
    "8",
    "--update-cycles",
    "12,20,27",
    "--decision-cycles",
    "17,24,31",
    "--analysts",
    "6",
    "--reviewers",
    "1",
    "--condition",
    "ordinary",
    "--update-flip",
    "flip",
    "--arm",
    "E",
    "--automatic-culling",
]


class TaskDataValidationTests(unittest.TestCase):
    def validate(self, seed):
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_workflow.py"),
                    "--output",
                    str(Path(tmp) / "o"),
                    "--validate-only",
                    "--seed",
                    str(seed),
                    *ROLLING,
                ],
                capture_output=True,
                text=True,
            )

    def test_seed_without_flip_data_fails_validation(self):
        result = self.validate(302)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("task data", result.stderr)

    def test_valid_seed_passes(self):
        self.assertEqual(self.validate(323).returncode, 0)


if __name__ == "__main__":
    unittest.main()
