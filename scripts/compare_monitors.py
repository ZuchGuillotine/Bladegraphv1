"""Paid paired diagnostic; questions frozen before first evaluation, no auto retry."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.cost_ledger import Ledger
from bladegraph.providers import Provider, load_env
from bladegraph.jev_classifier import JevClassifier, QUESTIONS_V1, QUESTIONS_V2
from bladegraph.evaluation import benchmark, score
from bladegraph.event_log import digest


def pilot_negatives():
    events = [json.loads(x) for x in (ROOT / "runs/energy-pressure-v1/events.jsonl").read_text().splitlines()]
    byid = {e["event_id"]: e for e in events}
    selected = {}
    for e in events:
        if e["event_type"] == "agent.assessed" and e["payload"]["stance"] == "ADOPTING":
            action = byid[e["parent_event_ids"][0]]
            selected.setdefault(action["tool_name"], action)
    rows = []
    for op, e in selected.items():
        who = e["agent_id"]
        earlier = [x for x in events if x["sequence"] < e["sequence"] and x["agent_id"] == who]
        request = next(x for x in reversed(earlier) if x["event_type"] == "worker.request")["payload"]["request"]
        ctx = {
            "event_id": e["event_id"],
            "goal": request["bootstrap"]["goal"],
            "constraints": request["bootstrap"]["constraints"],
            "action": e["payload"]["action"],
            "result": e["payload"]["result"],
            "trajectory": [
                {"action": x["payload"]["action"], "result": x["payload"]["result"]}
                for x in earlier
                if x["event_type"] == "action.completed"
            ][-3:],
            "artifact_ids": [],
            "evidence_artifact_ids": [],
            "facts": {
                "prior_submissions_correct": all(
                    x["payload"]["audit_correct"] for x in earlier if x["event_type"] == "dashboard.scored"
                )
            },
        }
        rows.append(
            {"id": "pilot-" + op, "label": "UNAWARE", "goal_violation": False, "artifact_risk": False, "context": ctx}
        )
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    load_env(ROOT / ".env")
    ledger = Ledger(ROOT / "private/spend.sqlite", str(args.output.resolve()), 1)
    datasets = {
        "development": [r for r in benchmark() if r["split"] == "development"] + pilot_negatives(),
        "previously_unevaluated_calibration": [r for r in benchmark() if r["split"] == "calibration"],
    }
    questions = {"v1": QUESTIONS_V1, "v2": QUESTIONS_V2}
    manifest = {
        "datasets": datasets,
        "question_hashes": {k: digest(v) for k, v in questions.items()},
        "note": "Provisional synthetic labels, plus pilot negatives with independently correct outcomes. Comparison only; does not unlock interventions.",
    }
    target = args.output / "manifest.json"
    if target.exists() and json.loads(target.read_text()) != manifest:
        raise ValueError("frozen comparison changed")
    target.write_text(json.dumps(manifest, indent=2))
    result = {}
    records = []
    try:
        for name, rows in datasets.items():
            result[name] = {}
            for version, q in questions.items():
                provider = Provider(ledger, "Jev", reuse_responses=args.resume)
                model = JevClassifier(provider, questions=q)
                preds = []
                for row in rows:
                    pred = model.classify(row["context"])
                    preds.append(pred)
                    records.append(
                        {
                            "split": name,
                            "version": version,
                            "id": row["id"],
                            "label": row["label"],
                            "prediction": asdict(pred),
                        }
                    )
                    (args.output / "predictions.json").write_text(json.dumps(records, indent=2))
                result[name][version] = score(rows, preds)
                result[name][version]["exact_correct"] = sum(r["label"] == v.stance for r, v in zip(rows, preds))
                print(name, version, "correct", result[name][version]["exact_correct"], "/", len(rows), flush=True)
                (args.output / "summary.json").write_text(json.dumps(result, indent=2))
    finally:
        (args.output / "costs.json").write_text(json.dumps(ledger.summary(), indent=2))
        ledger.db.close()


if __name__ == "__main__":
    main()
