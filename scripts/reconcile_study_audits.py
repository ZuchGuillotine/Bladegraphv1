"""Preserve historical metrics; write a separate audit correction after the provenance fix."""

import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.cooperative import CooperativeRuntime
from bladegraph.event_log import EventLog
from bladegraph.graph_projector import Graph
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.reviewer import FixtureReviewer


def reconcile(path):
    rows = [json.loads(x) for x in (path / "events.jsonl").read_text().splitlines()]
    log = EventLog()
    rt = CooperativeRuntime(log, Controller(FixtureClassifier(), FixtureReviewer(), Policy(), "A"), seed=7)
    rt.graph = Graph.replay(rows)
    results = []
    for e in rows:
        if e["event_type"] == "scorecard.audited":
            a = rt.graph.artifacts[e["payload"]["artifact_id"]]
            results.append(
                {
                    "artifact_id": a.artifact_id,
                    "author": a.author,
                    "content": a.content,
                    "original_official_audit": e["payload"]["correct"],
                    "corrected_audit": rt.audit_card(a),
                }
            )
    out = {
        "note": "Post-run factual audit with corrected transitive provenance and explicit cohort semantics. Original logs/metrics unchanged.",
        "source_run": path.name,
        "cards": results,
        "corrected_untruthful_scorecards": sum(not r["corrected_audit"]["truthful_for_stated_cohort"] for r in results),
        "valid_diagnostics": sum(
            r["corrected_audit"]["truthful_for_stated_cohort"] and not r["corrected_audit"]["official_eligible"]
            for r in results
        ),
        "reversed_false_audit_failures": sum(
            not r["original_official_audit"] and r["corrected_audit"]["official_eligible"] for r in results
        ),
    }
    (path / "audit-reconciliation.json").write_text(json.dumps(out, indent=2))
    log.close()
    print(path.name, {k: v for k, v in out.items() if k not in {"cards", "note"}})


if __name__ == "__main__":
    for name in sys.argv[1:]:
        reconcile(ROOT / "runs" / name)
