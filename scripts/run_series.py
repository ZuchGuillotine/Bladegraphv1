"""Frozen series with an enforced calibration gate, auditable completion receipts and no retries.
Only an unchanged, normally terminated and audited episode can be skipped on resume.
The accepted policy is local to this series and bound to its calibration and source/config.
"""

import argparse
import hashlib
import sqlite3
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.researcher_actions import scripted_fault_workers
from bladegraph.calibration import combine, evaluate, gate_metrics, load_profile, profile_entry, propose_policy
from bladegraph.event_log import EventLog, digest
from bladegraph.providers import load_env

PROFILES = ROOT / "experiments" / "policy-profiles.json"
BOUNDS = ROOT / "experiments" / "calibration-bounds.json"


def load_manifest(path: Path) -> dict:
    m = json.loads(path.read_text())
    for key in ("series", "model", "worker_prices", "common", "episodes", "series_budget_usd"):
        if key not in m:
            raise ValueError(f"manifest lacks {key}")
    names = [e["name"] for e in m["episodes"]]
    if len(set(names)) != len(names):
        raise ValueError("episode names must be unique")
    for e in m["episodes"]:
        if e.get("role") not in {"calibration", "gate", "scored"}:
            raise ValueError(f"episode {e['name']} needs role calibration, gate or scored")
        if e["role"] != "gate" and "args" not in e:
            raise ValueError(f"episode {e['name']} needs args")
    return m


def build_command(
    manifest: dict, episode: dict, output: Path, profiles_file: Path | None = None
) -> tuple[list[str], str | None]:
    """The run_workflow command for one episode and the policy profile it will use, if any."""
    model = manifest["model"]
    price_in, price_out = manifest["worker_prices"]
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_workflow.py"),
        "--output",
        str(output),
        "--worker-model",
        model,
        "--worker-provider",
        manifest.get("worker_provider", "openrouter"),
        "--worker-input-price",
        str(price_in),
        "--worker-output-price",
        str(price_out),
        *manifest["common"],
        *episode["args"],
    ]
    profile = None
    wanted = episode.get("policy_profile")
    if wanted:
        profile = model if wanted == "auto" else wanted
        cmd += ["--policy-profile", profile]
        if profiles_file:
            cmd += ["--policy-profiles-file", str(profiles_file)]
    return cmd, profile


def validate_commands(manifest, base):
    """Exercise the actual workflow parser for every arm before spending on calibration."""
    for episode in manifest["episodes"]:
        if episode["role"] == "gate":
            continue
        command, _ = build_command(manifest, episode, base / episode["name"], base / "accepted-policy.json")
        result = subprocess.run(command + ["--validate-only"], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"invalid episode command {episode['name']}: {result.stderr.strip()}")


def plan(manifest: dict, base: Path) -> list[dict]:
    rows = []
    for e in manifest["episodes"]:
        out = base / e["name"]
        if e["role"] == "gate":
            rows.append({"name": e["name"], "role": "gate", "done": (base / f"{e['name']}.json").exists()})
            continue
        cmd, profile = build_command(manifest, e, out)
        rows.append(
            {
                "name": e["name"],
                "role": e["role"],
                "done": completed_episode(out),
                "command": cmd,
                "profile": profile,
            }
        )
    return rows


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze_binding(manifest):
    paths = sorted((ROOT / "bladegraph").glob("*.py")) + [
        ROOT / "scripts" / name for name in ("run_series.py", "run_workflow.py", "run_study.py")
    ]
    provider_fields = (
        "MODEL",
        "PROVIDER",
        "MAX_OUTPUT_TOKENS",
        "REASONING_EFFORT",
        "PROVIDER_ORDER",
        "USAGE_ACCOUNTING",
        "INPUT_USD_PER_MILLION",
        "OUTPUT_USD_PER_MILLION",
    )
    return digest(
        {
            "manifest": manifest,
            "bounds": file_hash(BOUNDS),
            "source": {str(p.relative_to(ROOT)): file_hash(p) for p in paths},
            "providers": {
                prefix + "_" + k: os.environ.get(prefix + "_" + k)
                for prefix in ("REVIEWER", "JEV")
                for k in provider_fields
            },
        }
    )


def completed_episode(run, binding=None):
    receipt = run / "episode-complete.json"
    if not receipt.exists():
        return False
    try:
        data = json.loads(receipt.read_text())
        if binding is not None and data.get("binding") != binding:
            return False
        if not data.get("audits_passed"):
            return False
        return all(file_hash(run / name) == value for name, value in data["files"].items())
    except (OSError, ValueError, KeyError):
        return False


def record_completion(run, binding):
    termination = json.loads((run / "termination.json").read_text())
    if termination.get("status") != "completed":
        raise ValueError("episode did not terminate normally")
    boundaries = json.loads((run / "boundaries.json").read_text())
    rows = boundaries if isinstance(boundaries, list) else boundaries.get("runs", [])
    if not rows or any(row.get("review_flags") for row in rows):
        raise ValueError("boundary audit failed")
    receipt = {
        "binding": binding,
        "audits_passed": True,
        "files": {
            name: file_hash(run / name)
            for name in ("metrics.json", "termination.json", "boundaries.json", "summary.json", "events.jsonl")
        },
    }
    (run / "episode-complete.json").write_text(json.dumps(receipt, indent=2) + "\n")


def require_gate(report, binding, base):
    if (
        report.get("binding") != binding
        or report.get("accepted") is not True
        or report.get("verdict", {}).get("pass") is not True
        or report.get("proposal", {}).get("status") != "proposed"
        or report.get("proposal", {}).get("positive_controls_verified") is not True
    ):
        raise ValueError("calibration gate is not accepted for this frozen series")
    if file_hash(base / "accepted-policy.json") != report.get("profile_hash"):
        raise ValueError("accepted policy was changed")
    for name, hashes in report["calibration_hashes"].items():
        if not completed_episode(base / name, binding):
            raise ValueError("calibration completion receipt is missing or changed")
        for filename, value in hashes.items():
            if file_hash(base / name / filename) != value:
                raise ValueError("calibration evidence changed")


def run_gate(manifest: dict, base: Path, episode: dict, binding=None) -> dict:
    binding = binding or freeze_binding(manifest)
    model = manifest["model"]
    rows, names, hashes = [], [], {}
    for e in manifest["episodes"]:
        if e["role"] != "calibration":
            continue
        run = base / e["name"]
        if not completed_episode(run, binding):
            raise ValueError("gate requires every calibration episode and its audits")
        config = json.loads((run / "metrics.json").read_text())["config"]
        if config["worker_model"] != model:
            raise ValueError("calibration model differs from series")
        log = EventLog(run / "events.sqlite")
        try:
            log.verify()
            rows.append(gate_metrics(log.events(), config))
        finally:
            log.close()
        names.append(run.name)
        hashes[run.name] = {name: file_hash(run / name) for name in ("metrics.json", "events.jsonl")}
    if not rows:
        raise ValueError("gate needs completed calibration episodes")
    bounds = json.loads(BOUNDS.read_text())
    pooled = combine(rows)
    verdict = evaluate(pooled, bounds)
    proposal = propose_policy(pooled, rows[0]["policy"], bounds)
    accepted = (
        verdict["pass"] and proposal.get("status") == "proposed" and proposal.get("positive_controls_verified") is True
    )
    report = {
        "model": model,
        "runs": names,
        "per_run": rows,
        "pooled": pooled,
        "verdict": verdict,
        "proposal": proposal,
        "accepted": accepted,
        "binding": binding,
        "calibration_hashes": hashes,
    }
    if accepted:
        entry = profile_entry(model, pooled, proposal, names)
        entry.update(accepted=True, binding=binding)
        profile_path = base / "accepted-policy.json"
        profile_path.write_text(json.dumps({model: entry}, indent=2, default=str) + "\n")
        report["profile_hash"] = file_hash(profile_path)
    (base / f"{episode['name']}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


def spent(base: Path) -> float:
    total = 0.0
    for metrics in base.glob("*/metrics.json"):
        total += json.loads(metrics.read_text()).get("total_api_usd", 0.0)
    ledger = Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite")))
    if ledger.exists():
        db = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
        try:
            prefix = str(base) + os.sep
            total = max(
                total,
                db.execute(
                    "SELECT coalesce(sum(usd),0) FROM costs WHERE substr(run,1,?)=?", (len(prefix), prefix)
                ).fetchone()[0],
            )
        finally:
            db.close()
    return total


def post_run(run: Path, episode: dict):
    scripts = ROOT / "scripts"
    env = dict(os.environ)
    subprocess.run(
        [sys.executable, str(scripts / "summarize_workflows.py"), "--output", str(run / "summary.json"), str(run)],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            sys.executable,
            str(scripts / "audit_workflow_boundaries.py"),
            "--output",
            str(run / "boundaries.json"),
            str(run),
        ],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [sys.executable, str(scripts / "render_transcript.py"), str(run), "--output", str(run / "transcript.md")],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    config = json.loads((run / "metrics.json").read_text())["config"]
    agents = ["worker-12:g0"]
    for w in config.get("insiders", []) if config.get("condition") == "insider" else []:
        agents += [f"{w}:g0", f"{w}:g1"]
    for w in scripted_fault_workers(config):
        agents += [w + ":g0", w + ":g1"]
    for key in agents:
        subprocess.run(
            [
                sys.executable,
                str(scripts / "review_agent_path.py"),
                str(run),
                "--agent",
                key,
                "--output",
                str(run / f"path-{key.replace(':', '-')}.json"),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def manifest_environment(manifest: dict) -> dict:
    environment = manifest.get("environment", {})
    for key in environment:
        if (
            not key.startswith(("WORKER_", "REVIEWER_", "JEV_"))
            or any(x in key for x in ("KEY", "AUTH", "TOKEN"))
            and not key.endswith("MAX_OUTPUT_TOKENS")
        ):
            raise ValueError("manifest may configure providers, never credentials")
    return environment


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("--runs-root", type=Path, default=ROOT / "runs")
    p.add_argument("--dry-run", action="store_true")
    stop = p.add_mutually_exclusive_group()
    stop.add_argument("--through-block", help="stop after the named block; resume the same manifest later")
    stop.add_argument("--through-episode", help="stop after one named non-gate episode for treatment inspection")
    args = p.parse_args()
    manifest = load_manifest(args.manifest)
    if args.through_block and args.through_block not in {e.get("block") for e in manifest["episodes"]}:
        p.error("unknown block")
    if args.through_episode and args.through_episode not in {
        e["name"] for e in manifest["episodes"] if e["role"] != "gate"
    }:
        p.error("unknown non-gate episode")
    base = args.runs_root / manifest["series"]
    validate_commands(manifest, base)
    base.mkdir(parents=True, exist_ok=True)
    load_env(ROOT / ".env")
    for key, value in manifest_environment(manifest).items():
        os.environ[key] = str(value)
    if args.dry_run:
        for row in plan(manifest, base):
            print(json.dumps({k: v for k, v in row.items() if k != "command"}))
        return
    binding = freeze_binding(manifest)
    status_path = base / "series.json"
    status = (
        json.loads(status_path.read_text())
        if status_path.exists()
        else {"series": manifest["series"], "binding": binding, "episodes": {}}
    )
    if status.get("binding") != binding:
        raise ValueError("series source/config changed; preserve this attempt and start a newly named series")
    status_path.write_text(json.dumps(status, indent=2) + "\n")
    (base / "frozen-series.json").write_text(json.dumps({"binding": binding, "manifest": manifest}, indent=2) + "\n")
    gate = None
    reached_block = False
    for episode in manifest["episodes"]:
        name = episode["name"]
        if reached_block and episode.get("block") != args.through_block:
            break
        if episode["role"] == "gate":
            gate_path = base / f"{name}.json"
            gate = (
                json.loads(gate_path.read_text()) if gate_path.exists() else run_gate(manifest, base, episode, binding)
            )
            status["episodes"][name] = {"role": "gate", "accepted": gate.get("accepted"), "finished": time.time()}
            status_path.write_text(json.dumps(status, indent=2) + "\n")
            print("gate", name, "accepted", gate.get("accepted"), flush=True)
            require_gate(gate, binding, base)
            continue
        if episode["role"] == "scored":
            if gate is None:
                raise ValueError("scored episode requires an accepted gate")
            require_gate(gate, binding, base)
            if episode.get("policy_profile") != "auto":
                raise ValueError("scored episode must use this series' accepted profile")
        out = base / name
        if completed_episode(out, binding):
            print("audited episode already complete", name, flush=True)
        else:
            if out.exists() or name in status["episodes"]:
                raise ValueError(f"incomplete or changed episode {name}; retained, not silently retried or skipped")
            remaining = manifest["series_budget_usd"] - spent(base)
            cmd, profile = build_command(
                manifest, episode, out, base / "accepted-policy.json" if episode["role"] == "scored" else None
            )
            # The remaining series ceiling also caps this episode, including unsettled reservations.
            per_run = float(cmd[cmd.index("--budget") + 1])
            if remaining <= 0:
                raise ValueError("series budget exhausted")
            cmd += ["--budget", str(min(per_run, remaining))]
            print("episode", name, "profile", profile, flush=True)
            started = time.time()
            row = {"role": episode["role"], "profile": profile, "command": cmd, "started": started}
            status["episodes"][name] = row
            status_path.write_text(json.dumps(status, indent=2) + "\n")
            with (base / f"{name}.log").open("x") as log:
                code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ)).returncode
            row.update(exit=code, seconds=round(time.time() - started))
            if code == 0:
                try:
                    post_run(out, episode)
                    record_completion(out, binding)
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
                "finished",
                name,
                {k: row.get(k) for k in ("exit", "seconds", "usd", "complete", "audit_error")},
                flush=True,
            )
            if code:
                raise RuntimeError("episode/audit failed; series stopped without retry")
        if args.through_block and episode.get("block") == args.through_block:
            reached_block = True
        if name == args.through_episode:
            break
    print("series spent", round(spent(base), 4), flush=True)


if __name__ == "__main__":
    main()
