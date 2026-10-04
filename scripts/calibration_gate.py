"""Offline calibration gate for one fleet model: verify the fixed policy against preregistered
bounds over recorded runs and, on request, propose a per-model threshold profile."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.calibration import combine, evaluate, gate_metrics, profile_entry, propose_policy
from bladegraph.event_log import EventLog


def main():
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="+", type=Path, help="run directories with events.sqlite and metrics.json")
    p.add_argument("--model", required=True, help="fleet model id the runs share")
    p.add_argument("--bounds", type=Path, default=ROOT / "experiments" / "calibration-bounds.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--propose-policy", action="store_true", help="write a per-model profile to experiments/policy-profiles.json"
    )
    p.add_argument("--profiles", type=Path, default=ROOT / "experiments" / "policy-profiles.json")
    args = p.parse_args()
    bounds = json.loads(args.bounds.read_text())
    rows, names = [], []
    for run in args.runs:
        config = json.loads((run / "metrics.json").read_text())["config"]
        if config.get("worker_model") != args.model:
            p.error(f"{run} was not run with {args.model}")
        log = EventLog(run / "events.sqlite")
        log.verify()
        rows.append(gate_metrics(log.events(), config))
        log.close()
        names.append(run.name)
    pooled = combine(rows)
    verdict = evaluate(pooled, bounds)
    current = rows[0]["policy"]
    proposal = propose_policy(pooled, current, bounds)
    report = {
        "model": args.model,
        "runs": names,
        "per_run": rows,
        "pooled": pooled,
        "verdict": verdict,
        "proposal": proposal,
        "bounds": bounds,
    }
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(
        "gate", "PASS" if verdict["pass"] else "FAIL", json.dumps({k: v["pass"] for k, v in verdict["checks"].items()})
    )
    print(
        "proposal",
        json.dumps(
            {k: proposal.get(k) for k in ("status", "quarantine", "restrict", "cull", "positive_controls_verified")}
        ),
    )
    if args.propose_policy:
        profiles = json.loads(args.profiles.read_text()) if args.profiles.exists() else {}
        profiles[args.model] = profile_entry(args.model, pooled, proposal, names)
        args.profiles.write_text(json.dumps(profiles, indent=2, default=str) + "\n")
        print("profile written", args.profiles)


if __name__ == "__main__":
    main()
