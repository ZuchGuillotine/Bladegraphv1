"""Continue a frozen series past a retained, owner-excluded stopped episode, one named block at a time.

run_series.py refuses to pass an incomplete episode, which keeps a stopped paid episode from being
silently retried or skipped. When the owner decides to exclude such an episode (the preregistered
rule drops its block from paired comparisons) and continue with a later block, this researcher-only
tool runs exactly the named block with run_series' own functions, so the series binding (source,
manifest, bounds, reviewer/Jev configuration) is unchanged and checked. It never deletes, renames or
reruns the excluded episode, never retries a failed episode, requires the accepted series gate and
an audited completion receipt for every earlier episode that is not excluded, and stops on the first
failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_series as rs  # noqa: E402
from bladegraph.providers import load_env  # noqa: E402


def check_history(manifest: dict, base: Path, block: str, excluded: set, binding: str) -> list:
    """Every non-gate episode before the block must be audited complete or an excluded retained stop."""
    names = [e["name"] for e in manifest["episodes"]]
    first = next(i for i, e in enumerate(manifest["episodes"]) if e.get("block") == block)
    unknown = excluded - set(names[:first])
    if unknown:
        raise ValueError(f"excluded episodes must precede the block: {sorted(unknown)}")
    for episode in manifest["episodes"][:first]:
        if episode["role"] == "gate":
            continue
        out = base / episode["name"]
        complete = rs.completed_episode(out, binding)
        if episode["name"] in excluded:
            if complete:
                raise ValueError(f"{episode['name']} completed normally; it cannot be excluded")
            if not out.exists():
                raise ValueError(f"{episode['name']} has no retained evidence to exclude")
        elif not complete:
            raise ValueError(f"{episode['name']} is not audited complete; exclude it explicitly or stop")
    return [e for e in manifest["episodes"] if e.get("block") == block]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("--block", required=True)
    p.add_argument("--exclude", action="append", default=[], help="retained stopped episode the owner excluded")
    p.add_argument("--reason", required=True, help="owner decision recorded with each exclusion")
    p.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    manifest = rs.load_manifest(args.manifest)
    if args.block not in {e.get("block") for e in manifest["episodes"]}:
        p.error("unknown block")
    base = args.runs_root / manifest["series"]
    status_path = base / "series.json"
    if not status_path.exists():
        p.error("no series to continue")
    rs.validate_commands(manifest, base)
    load_env(ROOT / ".env")
    for key, value in rs.manifest_environment(manifest).items():
        os.environ[key] = str(value)
    binding = rs.freeze_binding(manifest)
    status = json.loads(status_path.read_text())
    if status.get("binding") != binding:
        raise ValueError("series source/config changed; preserve this attempt and start a newly named series")
    gate_episode = next(e for e in manifest["episodes"] if e["role"] == "gate")
    gate = json.loads((base / f"{gate_episode['name']}.json").read_text())
    rs.require_gate(gate, binding, base)
    block = check_history(manifest, base, args.block, set(args.exclude), binding)
    if args.dry_run:
        for e in block:
            print(json.dumps({"name": e["name"], "done": rs.completed_episode(base / e["name"], binding)}))
        return
    for name in args.exclude:
        row = status["episodes"].setdefault(name, {})
        row.setdefault("excluded", {"reason": args.reason, "excluded_at": time.time(), "retained": True})
    status_path.write_text(json.dumps(status, indent=2) + "\n")
    for episode in block:
        name = episode["name"]
        out = base / name
        if rs.completed_episode(out, binding):
            print("audited episode already complete", name, flush=True)
            continue
        if out.exists() or name in status["episodes"]:
            raise ValueError(f"incomplete or changed episode {name}; retained, not silently retried or skipped")
        if episode.get("policy_profile") != "auto":
            raise ValueError("scored episode must use this series' accepted profile")
        rs.require_gate(gate, binding, base)
        remaining = manifest["series_budget_usd"] - rs.spent(base)
        cmd, profile = rs.build_command(manifest, episode, out, base / "accepted-policy.json")
        per_run = float(cmd[cmd.index("--budget") + 1])
        if remaining <= 0:
            raise ValueError("series budget exhausted")
        cmd += ["--budget", str(min(per_run, remaining))]
        print("episode", name, "profile", profile, flush=True)
        started = time.time()
        row = {
            "role": episode["role"],
            "profile": profile,
            "command": cmd,
            "started": started,
            "via": "continue_series",
        }
        status["episodes"][name] = row
        status_path.write_text(json.dumps(status, indent=2) + "\n")
        with (base / f"{name}.log").open("x") as log:
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ)).returncode
        row.update(exit=code, seconds=round(time.time() - started))
        if code == 0:
            try:
                rs.post_run(out, episode)
                rs.record_completion(out, binding)
                from scripts.cloud_pilot import bundle

                bundle(out, Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite"))))
                m = json.loads((out / "metrics.json").read_text())
                row.update(
                    usd=m.get("total_api_usd"), complete=m.get("task_complete"), recovery=m.get("recovery_delivery")
                )
            except Exception as exc:
                row["audit_error"] = str(exc)
                code = 1
        status_path.write_text(json.dumps(status, indent=2) + "\n")
        print(
            "finished", name, {k: row.get(k) for k in ("exit", "seconds", "usd", "complete", "audit_error")}, flush=True
        )
        if code:
            raise RuntimeError("episode/audit failed; series stopped without retry")
    print("series spent", round(rs.spent(base), 4), flush=True)


if __name__ == "__main__":
    main()
