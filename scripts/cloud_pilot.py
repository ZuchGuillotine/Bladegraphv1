#!/usr/bin/env python3
"""Researcher launcher. Run in a Claude cloud session; never give it to swarm workers."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
import fcntl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.providers import load_env


def bundle(output, ledger):
    # Exact allowlist; never archive the checkout, environment or credentials.
    snapshot = output / "spend.sqlite"
    with closing(sqlite3.connect(ledger)) as src, closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst)
    paths = [
        p
        for p in output.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and p != output / "checksums.json"
        and p.suffix in {".json", ".jsonl", ".sqlite", ".html", ".log"}
    ]
    secrets = [v.encode() for k, v in os.environ.items() if k.endswith(("_API_KEY", "_TOKEN")) and len(v) > 12]
    for p in paths:
        data = p.read_bytes()
        if any(s in data for s in secrets):
            raise RuntimeError("credential scan failed; bundle was not created")
    manifest = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    (output / "checksums.json").write_text(json.dumps(manifest, indent=2))
    archive = output.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        for p in paths + [output / "checksums.json"]:
            tar.add(p, arcname=output.name + "/" + str(p.relative_to(output)), recursive=False)
    archive.chmod(0o600)
    archive.with_suffix(archive.suffix + ".sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "\n")
    return archive


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--ledger", type=Path, required=True, help="Restored previous spend.sqlite; never start a fresh cloud ledger"
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--condition", choices=["pressure", "seeded", "both"], default="both")
    p.add_argument("--cycles", type=int, default=12)
    p.add_argument("--budget", type=float, default=1, help="USD cap per condition, including monitor")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument(
        "--upload", action="store_true", help="Save bundle to a draft release in the private project repository"
    )
    args = p.parse_args()
    load_env(ROOT / ".env")
    if not args.ledger.is_file():
        p.error("Restore the previous ledger before starting. See docs/claude-cloud.md.")
    with closing(sqlite3.connect(args.ledger)) as db:
        if db.execute("SELECT count(*) FROM sqlite_master WHERE name='costs'").fetchone()[0] != 1:
            p.error("Invalid ledger: restore an actual previous project spend ledger")
    if args.output.exists():
        p.error("Choose a fresh output directory; completed paid requests are not retried.")
    if not 1 <= args.cycles <= 16 or not 0 < args.budget <= 1:
        p.error("Pilot limits: 1..16 cycles and at most $1 per condition")
    args.output = args.output.resolve()
    args.ledger = args.ledger.resolve()
    if args.upload:
        visibility = subprocess.check_output(
            ["gh", "repo", "view", "ZuchGuillotine/BladeGraph", "--json", "isPrivate", "--jq", ".isPrivate"], text=True
        ).strip()
        if visibility != "true":
            raise RuntimeError("refusing research upload to non-private repository")
    lock = args.ledger.with_suffix(".lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    args.output.mkdir(parents=True)
    config = {
        "condition": args.condition,
        "cycles": args.cycles,
        "seed": args.seed,
        "budget_per_condition": args.budget,
        "initial_agents": 13,
        "generations_per_condition": 1,
        "arm": "B",
        "scenario": "energy-audit",
        "claude_session_id": os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID"),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (args.output / "launch.json").write_text(json.dumps(config, indent=2))
    env = {**os.environ, "BLADEGRAPH_LEDGER": str(args.ledger)}
    conditions = ["pressure", "seeded"] if args.condition == "both" else [args.condition]
    status = 0
    try:
        for condition in conditions:
            run = args.output / condition
            command = [
                sys.executable,
                "-m",
                "bladegraph.cli",
                "live",
                "--arm",
                "B",
                "--scenario",
                "energy-audit",
                "--cycles",
                str(args.cycles),
                "--seed",
                str(args.seed),
                "--budget",
                str(args.budget),
                "--output",
                str(run),
            ]
            if condition == "seeded":
                command.append("--seeded-route")
            print("Starting " + condition, flush=True)
            with (args.output / (condition + ".log")).open("w") as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            if completed.returncode:
                status = completed.returncode
                break
    finally:
        # Preserve even interrupted/failed runs and the provider records billed to them.
        audit = args.output / "provider-responses"
        audit.mkdir()
        with closing(sqlite3.connect(args.ledger)) as db:
            for condition in conditions:
                for (key,) in db.execute("select id from costs where run=?", (str(args.output / condition),)):
                    source = args.ledger.parent / "provider-responses" / (key + ".json")
                    if source.exists():
                        (audit / source.name).write_bytes(source.read_bytes())
        archive = bundle(args.output, args.ledger)
        print("Review bundle: " + str(archive), flush=True)
        if args.upload:
            visibility = subprocess.check_output(
                ["gh", "repo", "view", "ZuchGuillotine/BladeGraph", "--json", "isPrivate", "--jq", ".isPrivate"],
                text=True,
            ).strip()
            if visibility != "true":
                raise RuntimeError("repository must still be private before upload")
            tag = "pilot-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            subprocess.run(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    str(archive),
                    str(archive) + ".sha256",
                    "--repo",
                    "ZuchGuillotine/BladeGraph",
                    "--target",
                    subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "--draft",
                    "--title",
                    tag,
                    "--notes",
                    "Private research pilot evidence and spend ledger. Do not expose to experiment workers.",
                ],
                check=True,
            )
    raise SystemExit(status)


if __name__ == "__main__":
    main()
