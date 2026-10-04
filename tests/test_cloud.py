import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "cloud_pilot", Path(__file__).resolve().parents[1] / "scripts/cloud_pilot.py"
)
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class CloudBundleTests(unittest.TestCase):
    def setup_bundle(self, tmp):
        root = Path(tmp)
        out = root / "pilot"
        out.mkdir()
        ledger = root / "cost.sqlite"
        with closing(sqlite3.connect(ledger)) as db:
            db.execute("create table costs (id text, usd real)")
            db.execute("insert into costs values (?,?)", ("historical", 0.05))
            db.commit()
        (out / "metrics.json").write_text('{"completed":false}')
        (out / ".env").write_text("NEVER_ARCHIVE_THIS=value")
        return out, ledger

    def test_bundle_preserves_ledger_and_partial_evidence_excludes_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, ledger = self.setup_bundle(tmp)
            archive = cloud.bundle(out, ledger)
            with tarfile.open(archive) as tar:
                names = tar.getnames()
                self.assertNotIn("pilot/.env", names)
                self.assertIn("pilot/spend.sqlite", names)
                self.assertIn("pilot/metrics.json", names)
                self.assertIn("pilot/checksums.json", names)
            with closing(sqlite3.connect(out / "spend.sqlite")) as db:
                self.assertEqual(db.execute("select usd from costs").fetchone()[0], 0.05)

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-secret-123456789"})
    def test_bundle_refuses_credential_in_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, ledger = self.setup_bundle(tmp)
            (out / "leak.json").write_text('{"text":"synthetic-secret-123456789"}')
            with self.assertRaises(RuntimeError):
                cloud.bundle(out, ledger)
            self.assertFalse(out.with_suffix(".tar.gz").exists())

    def test_repackaged_bundle_has_unique_members_and_current_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, ledger = self.setup_bundle(tmp)
            cloud.bundle(out, ledger)
            (out / "metrics.json").write_text('{"completed":true}')
            with tarfile.open(cloud.bundle(out, ledger)) as tar:
                names = tar.getnames()
                self.assertEqual(len(names), len(set(names)))
                manifest = json.load(tar.extractfile("pilot/checksums.json"))
                self.assertNotIn("checksums.json", manifest)
                for name, expected in manifest.items():
                    self.assertEqual(hashlib.sha256(tar.extractfile("pilot/" + name).read()).hexdigest(), expected)
