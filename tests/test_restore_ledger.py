import hashlib
import importlib.util
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "restore_ledger", Path(__file__).resolve().parents[1] / "scripts/restore_ledger.py"
)
restore_ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore_ledger)


def make_ledger(path, rows):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE costs (id TEXT PRIMARY KEY, run TEXT, component TEXT, usd REAL, tokens INTEGER, status TEXT)"
        )
        db.executemany("INSERT INTO costs VALUES (?, 'r', 'Jev', ?, 0, ?)", rows)
        db.commit()


def make_bundle(root, name, ledger_rows):
    run = root / name
    run.mkdir()
    make_ledger(run / "spend.sqlite", ledger_rows)
    (run / "metrics.json").write_text("{}")
    archive = root / (name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for p in run.iterdir():
            tar.add(p, arcname=name + "/" + p.name)
    (root / (name + ".tar.gz.sha256")).write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")
    return archive


class FakeGitHub:
    def __init__(self, releases, files):
        self.releases, self.files, self.downloads = releases, files, []

    def json(self, path):
        assert path.startswith("/repos/ZuchGuillotine/BladeGraph/releases")
        return self.releases

    def download(self, url, target):
        self.downloads.append(url)
        target.write_bytes(self.files[url].read_bytes())


class RestoreLedgerTests(unittest.TestCase):
    def test_picks_most_complete_ledger_verifies_checksums_and_never_downgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            early = make_bundle(root, "run-a", [("1", 0.5, "settled")])
            late = make_bundle(root, "run-b", [("1", 0.5, "settled"), ("2", 0.25, "settled"), ("3", 0.01, "reserved")])
            files = {
                "u/a": early,
                "u/a.sha": root / "run-a.tar.gz.sha256",
                "u/b": late,
                "u/b.sha": root / "run-b.tar.gz.sha256",
            }
            releases = [
                {
                    "id": 1,
                    "name": "old",
                    "tag_name": "old",
                    "created_at": "2026-09-24",
                    "assets": [
                        {"name": "run-a.tar.gz", "url": "u/a"},
                        {"name": "run-a.tar.gz.sha256", "url": "u/a.sha"},
                    ],
                },
                {
                    "id": 2,
                    "name": "workflow evidence",
                    "tag_name": "",
                    "created_at": "2026-09-25",
                    "assets": [
                        {"name": "run-a.tar.gz", "url": "u/a"},
                        {"name": "run-a.tar.gz.sha256", "url": "u/a.sha"},
                        {"name": "run-b.tar.gz", "url": "u/b"},
                        {"name": "run-b.tar.gz.sha256", "url": "u/b.sha"},
                    ],
                },
                {"id": 3, "name": "no bundle", "tag_name": "x", "created_at": "2026-09-26", "assets": []},
            ]
            github = FakeGitHub(releases, files)
            dest = root / "private/spend.sqlite"
            result = restore_ledger.restore(github, "ZuchGuillotine/BladeGraph", None, dest, root / "work")
            self.assertEqual(result["release"], "workflow evidence")
            self.assertEqual((result["rows"], result["unsettled_reservations"]), (3, 1))
            self.assertAlmostEqual(result["total_usd"], 0.76)
            self.assertTrue(all(b["verified"] for b in result["bundles"]))
            self.assertEqual(restore_ledger.ledger_stats(dest)[0], 3)
            # A second run keeps the fuller ledger and does not re-download cached assets.
            downloads = len(github.downloads)
            again = restore_ledger.restore(github, "ZuchGuillotine/BladeGraph", "2", dest, root / "work")
            self.assertTrue(again["kept_existing"])
            self.assertEqual(len(github.downloads), downloads)
            (root / "run-b.tar.gz.sha256").write_text("0" * 64 + "\n")
            (root / "work/run-b.tar.gz.sha256").unlink()
            with self.assertRaises(ValueError):
                restore_ledger.restore(
                    github, "ZuchGuillotine/BladeGraph", "2", root / "elsewhere.sqlite", root / "work2"
                )
            with self.assertRaises(ValueError):
                restore_ledger.select_release(releases, "no bundle")

    def test_restore_from_local_bundles_picks_fullest_and_never_downgrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = make_bundle(root, "run-a", [("1", 0.5, "settled")])
            big = make_bundle(root, "run-b", [("1", 0.5, "settled"), ("2", 0.25, "settled"), ("3", 0.01, "reserved")])
            dest = root / "private/spend.sqlite"
            result = restore_ledger.restore_from_bundles([small, big], dest, root / "work")
            self.assertEqual((result["rows"], result["unsettled_reservations"]), (3, 1))
            self.assertTrue(all(b["verified"] for b in result["bundles"]))
            again = restore_ledger.restore_from_bundles([small], dest, root / "work2")
            self.assertTrue(again["kept_existing"])
            self.assertEqual(restore_ledger.ledger_stats(dest)[0], 3)


if __name__ == "__main__":
    unittest.main()
