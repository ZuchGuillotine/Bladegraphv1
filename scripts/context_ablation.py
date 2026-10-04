"""Reconstruct exact prior monitor requests, reuse paid responses, test richer evidence."""

from pathlib import Path
import json, sys, sqlite3
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.event_log import digest
from bladegraph.jev_classifier import QUESTIONS_V1, JevClassifier
from bladegraph.providers import Provider, load_env
from bladegraph.cost_ledger import Ledger


def reconstruct(path):
    events = [json.loads(s) for s in (path / "events.jsonl").read_text().splitlines()]
    contexts = {}
    histories = {}
    artifacts = {}
    pending = {}
    requests = {}
    completed = {}
    rows = []
    for e in events:
        who = e["agent_id"] + ":g" + str(e["agent_generation"])
        p = e["payload"]
        kind = e["event_type"]
        contexts.setdefault(who, [])
        histories.setdefault(who, [])
        if kind == "worker.request":
            requests[who] = p["request"]
        elif kind == "artifact.created":
            artifacts[p["artifact_id"]] = p
            if e["agent_id"].startswith("worker"):
                pending.setdefault(who, []).append(p["artifact_id"])
        elif kind == "action.rejected":
            contexts[who].append({"op": "rejected", "result": {"status": "unavailable", "notice": p["reason"]}})
        elif kind == "action.completed":
            action = p["action"]
            result = p["result"]
            deps = action.get("derived_from", [])
            created = pending.pop(who, [])
            boot = requests[who]["bootstrap"]
            ctx = {
                "goal": boot["goal"],
                "constraints": boot["constraints"],
                "event_id": e["event_id"],
                "action": action,
                "result": result,
                "artifact_ids": created,
                "evidence_artifact_ids": sorted(set(created + deps)),
                "trajectory": contexts[who][-3:],
                "graph": [
                    {"id": key, "content": artifacts[key]["content"], "derived_from": artifacts[key]["derived_from"]}
                    for key in deps
                ],
            }
            enhanced = {
                **ctx,
                "trajectory": histories[who][-3:],
                "actor": who,
                "evidence_scope": "Classify the current action; prior actions provide context, not new conduct.",
            }
            completed[e["event_id"]] = (ctx, enhanced)
            visible = {k: v for k, v in result.items() if k != "claim_correct"}
            if created:
                a = artifacts[created[0]]
                visible = {
                    "id": a["artifact_id"],
                    "content": a["content"],
                    "derived_from": a["derived_from"],
                    "verification": a["verification_state"],
                }
            contexts[who].append({"op": action["op"], "result": visible})
            histories[who].append({"event_id": e["event_id"], "action": action, "result": result})
        elif kind == "agent.assessed" and p["stance"] == "ADOPTING":
            ctx, enhanced = completed[e["parent_event_ids"][0]]
            rows.append(
                {
                    "event_id": e["event_id"],
                    "original": ctx,
                    "enhanced": enhanced,
                    "old_prediction": e["classifier_result"],
                }
            )
    return rows


def main():
    load_env(ROOT / ".env")
    out = ROOT / "runs/context-ablation-v1"
    out.mkdir(exist_ok=True)
    if (out / "frozen-cases.json").exists():
        raise ValueError("existing paid comparison; do not rerun")
    rows = reconstruct(ROOT / "runs/energy-pressure-v1")
    # Frozen first two per action type, including the one correct submit. All are known-negative pilot diagnostics.
    counts = {}
    selected = []
    for row in rows:
        op = row["original"]["action"]["op"]
        counts[op] = counts.get(op, 0) + 1
        if counts[op] <= 2:
            selected.append(row)
    ledger = Ledger(ROOT / "private/spend.sqlite", str(out), 1)
    provider = Provider(ledger, "Jev")
    classifier = JevClassifier(provider, questions=QUESTIONS_V1)
    events = [json.loads(s) for s in (ROOT / "runs/energy-pressure-v1/events.jsonl").read_text().splitlines()]
    assessments = [e for e in events if e["event_type"] == "agent.assessed"]
    ids = [
        r[0]
        for r in ledger.db.execute(
            "select id from costs where run=? and component=? order by rowid",
            (str(ROOT / "runs/energy-pressure-v1"), "Jev"),
        )
    ]
    if len(ids) != len(assessments):
        raise ValueError("cannot align original accounting with assessment events")
    audit_ids = {e["event_id"]: key for e, key in zip(assessments, ids)}
    for row in selected:
        payload = {"state": row["original"], "questions": QUESTIONS_V1, "model": provider.model}
        key = digest(payload)
        newkey = digest({"endpoint": provider.endpoint, "payload": payload})
        audit_id = audit_ids[row["event_id"]]
        saved = json.loads((ROOT / "private/provider-responses" / (audit_id + ".json")).read_text())
        if saved["request_hash"] not in {key, newkey}:
            raise ValueError("prior request reconstruction did not match paid request hash")
        row["exact_original_audit_id"] = audit_id
    (out / "frozen-cases.json").write_text(json.dumps(selected, indent=2))
    results = []
    try:
        for row in selected:
            pred = classifier.classify(row["enhanced"])
            results.append(
                {
                    "event_id": row["event_id"],
                    "op": row["original"]["action"]["op"],
                    "old": row["old_prediction"],
                    "new": asdict(pred),
                }
            )
            (out / "results.json").write_text(json.dumps(results, indent=2))
            print(row["original"]["action"]["op"], pred.stance, round(pred.adoption, 3), flush=True)
    finally:
        (out / "costs.json").write_text(json.dumps(ledger.summary(), indent=2))
        ledger.db.close()


if __name__ == "__main__":
    main()
