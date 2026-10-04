"""Restore the cumulative spend ledger from the private evidence release before any paid run.

Researcher-only, standard library only, no model calls. Intended as the cloud environment's
setup script step: it downloads the evidence bundle(s) of one GitHub release, verifies the
published SHA-256 when present, extracts every spend.sqlite snapshot and merges unique reservation IDs across all snapshots as private/spend.sqlite. It never
downgrades an existing ledger and prints the totals so they can be compared with the
figures recorded in docs/phases.md.

Authentication: a GITHUB_TOKEN/GH_TOKEN environment variable is sent as a bearer token; when
absent, requests go out without one so a credential proxy matched to api.github.com can inject
it. Draft releases are visible only to tokens with push access to the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
USER_AGENT = "bladegraph-restore-ledger"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    """Minimal REST client. Asset downloads follow GitHub's one redirect without the bearer token,
    because the signed storage URL rejects a second authorization mechanism."""

    def __init__(self, token: str | None):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def _headers(self, accept: str, auth: bool) -> dict:
        headers = {"Accept": accept, "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def json(self, path: str):
        request = urllib.request.Request(API + path, headers=self._headers("application/vnd.github+json", True))
        with self.opener.open(request, timeout=60) as response:
            return json.loads(response.read())

    def download(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers=self._headers("application/octet-stream", True))
        try:
            with self.opener.open(request, timeout=120) as response:
                target.write_bytes(response.read())
                return
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308} or "Location" not in error.headers:
                raise
            location = error.headers["Location"]
        request = urllib.request.Request(location, headers=self._headers("application/octet-stream", False))
        with urllib.request.urlopen(request, timeout=300) as response:
            target.write_bytes(response.read())


def select_release(releases: list, selector: str | None) -> dict:
    candidates = [r for r in releases if any(a["name"].endswith(".tar.gz") for a in r.get("assets", []))]
    if selector:
        candidates = [
            r
            for r in candidates
            if selector in {str(r["id"]), r.get("tag_name", ""), r.get("name", "")}
            or selector in r.get("tag_name", "")
            or selector in r.get("name", "")
        ]
    if not candidates:
        raise ValueError("no release with a .tar.gz evidence bundle matched")
    return sorted(candidates, key=lambda r: r.get("created_at", ""))[-1]


def ledger_stats(path: Path) -> tuple[int, float, int]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        if db.execute("SELECT count(*) FROM sqlite_master WHERE name='costs'").fetchone()[0] != 1:
            raise ValueError(f"{path.name} has no costs table")
        rows, total = db.execute("SELECT count(*), coalesce(sum(usd), 0) FROM costs").fetchone()
        reserved = db.execute("SELECT count(*) FROM costs WHERE status='reserved'").fetchone()[0]
    return rows, float(total), reserved


def extract_ledgers(archive: Path, workdir: Path) -> list[Path]:
    found = []
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.isfile() and Path(member.name).name == "spend.sqlite" and ".." not in Path(member.name).parts:
                target = workdir / (archive.stem.replace(".tar", "") + "__" + member.name.replace("/", "__"))
                with tar.extractfile(member) as source:
                    target.write_bytes(source.read())
                found.append(target)
    return found


def choose_ledger(paths: list[Path]) -> Path:
    if not paths:
        raise ValueError("no spend.sqlite found in the downloaded bundles")
    return max(paths, key=lambda p: ledger_stats(p)[:2])


def verify_checksum(archive: Path, checksum_file: Path | None) -> str:
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if checksum_file is not None:
        expected = checksum_file.read_text().split()[0].strip()
        if expected != actual:
            raise ValueError(f"checksum mismatch for {archive.name}")
    return actual


def merge_ledgers(paths: list[Path], destination: Path) -> dict:
    """Union reservation IDs across forks. A settled row supersedes its reservation;
    conflicting settlements fail atomically. Existing local rows are never dropped."""
    if not paths:
        raise ValueError("no spend.sqlite snapshots supplied")
    incoming = []
    for path in paths:
        ledger_stats(path)
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            incoming.extend(db.execute("SELECT id,run,component,usd,tokens,status FROM costs"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(destination, timeout=30)) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS costs (id TEXT PRIMARY KEY, run TEXT, component TEXT, usd REAL, tokens INTEGER, status TEXT)"
        )
        db.execute("BEGIN IMMEDIATE")
        try:
            before = {r[0]: r for r in db.execute("SELECT id,run,component,usd,tokens,status FROM costs")}
            merged = dict(before)
            for row in incoming:
                old = merged.get(row[0])
                if row[5] not in {"reserved", "settled"}:
                    raise ValueError("unknown reservation status")
                if old is None or old == row:
                    merged[row[0]] = row
                elif old[1:3] != row[1:3]:
                    raise ValueError("reservation identity conflict: " + row[0])
                elif old[5] == "reserved" and row[5] == "settled":
                    merged[row[0]] = row
                elif old[5] == "settled" and row[5] == "reserved":
                    continue
                else:
                    raise ValueError("conflicting ledger records: " + row[0])
            changed = [row for key, row in merged.items() if before.get(key) != row]
            db.executemany("INSERT OR REPLACE INTO costs VALUES (?,?,?,?,?,?)", changed)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    rows, total, reserved = ledger_stats(destination)
    return {
        "kept_existing": not changed,
        "merged_snapshots": len(paths),
        "added_or_settled_rows": len(changed),
        "rows": rows,
        "total_usd": total,
        "unsettled_reservations": reserved,
    }


def restore(github: GitHub, repo: str, selector: str | None, destination: Path, workdir: Path) -> dict:
    releases = github.json(f"/repos/{repo}/releases?per_page=30")
    release = select_release(releases, selector)
    workdir.mkdir(parents=True, exist_ok=True)
    assets = {a["name"]: a for a in release["assets"]}
    ledgers, bundles = [], []
    for name, asset in sorted(assets.items()):
        if not name.endswith(".tar.gz"):
            continue
        archive = workdir / name
        if not archive.exists():
            github.download(asset["url"], archive)
        checksum = None
        if name + ".sha256" in assets:
            checksum = workdir / (name + ".sha256")
            if not checksum.exists():
                github.download(assets[name + ".sha256"]["url"], checksum)
        bundles.append({"asset": name, "sha256": verify_checksum(archive, checksum), "verified": checksum is not None})
        ledgers.extend(extract_ledgers(archive, workdir))
    return {"release": release.get("name"), **merge_ledgers(ledgers, destination), "bundles": bundles}


def restore_from_bundles(bundles: list[Path], destination: Path, workdir: Path) -> dict:
    """Merge spend.sqlite histories from local bundles without dropping forked spend."""
    workdir.mkdir(parents=True, exist_ok=True)
    ledgers, records = [], []
    for archive in bundles:
        checksum = archive.with_name(archive.name + ".sha256")
        records.append(
            {
                "bundle": archive.name,
                "sha256": verify_checksum(archive, checksum if checksum.exists() else None),
                "verified": checksum.exists(),
            }
        )
        ledgers.extend(extract_ledgers(archive, workdir))
    return {**merge_ledgers(ledgers, destination), "bundles": records}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--bundle",
        type=Path,
        action="append",
        default=None,
        help="local evidence bundle (.tar.gz) to restore from instead of a GitHub release; repeatable. "
        "Use this when the latest ledger lives on an evidence branch rather than in a release.",
    )
    p.add_argument("--repo", default=os.environ.get("BLADEGRAPH_EVIDENCE_REPO", "ZuchGuillotine/BladeGraph"))
    p.add_argument(
        "--release",
        default=os.environ.get("BLADEGRAPH_EVIDENCE_RELEASE"),
        help="release id, tag or name fragment; default: newest release carrying a bundle",
    )
    p.add_argument(
        "--destination",
        type=Path,
        default=Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite"))),
    )
    p.add_argument("--workdir", type=Path, default=ROOT / "private/evidence")
    args = p.parse_args(argv)
    if args.bundle:
        result = restore_from_bundles(args.bundle, args.destination, args.workdir)
    else:
        github = GitHub(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
        result = restore(github, args.repo, args.release, args.destination, args.workdir)
    print(json.dumps(result, indent=2))
    print(
        f"ledger at {args.destination}: {result['rows']} rows, ${result['total_usd']:.9f}, "
        f"{result['unsettled_reservations']} unsettled reservations",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
