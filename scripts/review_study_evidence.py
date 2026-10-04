"""Independent model review of selected diagnostics, without Jev predictions."""

from pathlib import Path
import json, sys
from dataclasses import asdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.cost_ledger import Ledger
from bladegraph.providers import Provider, load_env
from bladegraph.models import Review
from bladegraph.reviewer import REVIEWER_PROMPT


def main():
    out = ROOT / "runs/cooperative-review-v1"
    out.mkdir()
    contexts = {}
    allctx = []
    for name in ["cooperative-seeded-v1", "cooperative-seeded-v2"]:
        rows = [json.loads(s) for s in (ROOT / "runs" / name / "events.jsonl").read_text().splitlines()]
        for e in rows:
            if e["event_type"] == "monitor.context":
                ctx = e["payload"]["context"]
                allctx.append((name, ctx))
                for key in ctx.get("artifact_ids", []):
                    contexts[name, key] = ctx
    reconciled = json.loads((ROOT / "runs/cooperative-seeded-v2/audit-reconciliation.json").read_text())["cards"]
    material = next(r for r in reconciled if r["corrected_audit"]["expected_full_loss"] - r["content"]["loss"] > 10)
    small = next(
        r for r in reconciled if not r["corrected_audit"]["truthful_for_stated_cohort"] and r["content"]["loss"] == 50
    )
    valid = next(
        r for r in reconciled if not r["original_official_audit"] and r["corrected_audit"]["official_eligible"]
    )
    cases = [
        ("material_understatement", contexts["cooperative-seeded-v2", material["artifact_id"]]),
        ("small_unsupported_number", contexts["cooperative-seeded-v2", small["artifact_id"]]),
        ("valid_transitive_reuse", contexts["cooperative-seeded-v2", valid["artifact_id"]]),
    ]
    cases.append(
        (
            "diagnostic_evaluation",
            next(c for n, c in allctx if c["action"]["op"] == "evaluate" and c["action"]["cohort"] == "stable"),
        )
    )
    cases.append(
        (
            "ordinary_graph_read",
            next(
                c
                for n, c in allctx
                if c["action"]["op"] == "read" and c["result"].get("content", {}).get("kind") == "evaluation"
            ),
        )
    )
    # Remove derived verdict booleans; reviewer sees actual actions, source data and numeric facts.
    for name, ctx in cases:
        ctx["validation_facts"] = {
            k: v
            for k, v in ctx.get("validation_facts", {}).items()
            if k in {"expected_full_loss", "reported_loss", "actual_loss"}
        }
    (out / "cases.json").write_text(json.dumps(cases, indent=2))
    load_env(ROOT / ".env")
    ledger = Ledger(ROOT / "private/spend.sqlite", str(out), 1)
    provider = Provider(ledger, "reviewer")
    results = []
    try:
        for name, ctx in cases:
            try:
                review = Review(**provider.chat(REVIEWER_PROMPT, {"evidence": ctx}))
                ids = {ctx["event_id"]} | {v["event_id"] for v in ctx["trajectory"]}
                review.validate(ids, set(ctx["evidence_artifact_ids"]))
                result = {"case": name, "review": asdict(review)}
            except (ValueError, TypeError, KeyError) as exc:
                result = {"case": name, "invalid_review": type(exc).__name__}
            results.append(result)
            (out / "results.json").write_text(json.dumps(results, indent=2))
            print(result, flush=True)
    finally:
        (out / "usage.json").write_text(json.dumps(provider.records, indent=2))
        (out / "costs.json").write_text(json.dumps(ledger.summary(), indent=2))
        ledger.db.close()


if __name__ == "__main__":
    main()
