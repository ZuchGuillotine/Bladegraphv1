"""Offline research summary; never delivered to participants."""

import argparse, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.event_log import EventLog
from bladegraph.graph_projector import Graph


def summary_recipients(events, key):
    """Workers shown this ID in a passive summary (dashboard row or work-order pointer), which is
    not a READ. Runs before the passive-summary change have none of these events."""
    return sorted(
        {
            e["agent_id"] + ":g" + str(e["agent_generation"])
            for e in events
            if e["event_type"] == "artifact.summarized" and key in e["payload"]["artifact_ids"]
        }
    )


def lead_actions(events):
    # A rejected unsupported tool call is logged with an empty action and has no op.
    return Counter(
        e["payload"]["action"].get("op", "(unsupported)")
        for e in events
        if e["agent_id"] == "worker-12" and e["event_type"] in {"action.completed", "action.rejected"}
    )


def summarize(path):
    log = EventLog(path / "events.sqlite")
    log.verify()
    events = list(log.events())
    log.close()
    graph = Graph.replay(events)
    assert graph.snapshot() == json.loads((path / "graph.json").read_text())
    metrics = json.loads((path / "metrics.json").read_text())
    bad = [
        e for e in events if e["event_type"] == "scorecard.audited" and not e["payload"]["truthful_for_stated_cohort"]
    ]
    bad_ids = {e["payload"]["artifact_id"] for e in bad}
    details = []
    for event in bad:
        key = event["payload"]["artifact_id"]
        a = graph.artifacts[key]
        exposures = sorted(
            {
                e["agent_id"] + ":g" + str(e["agent_generation"])
                for e in events
                if e["event_type"] == "artifact.read" and key in e["artifact_ids_read"]
            }
        )
        validation = [
            {"cycle": e["payload"]["tick"], "actor": e["agent_id"], "passed": e["payload"]["passed"]}
            for e in events
            if e["event_type"] == "artifact.validated" and e["payload"]["artifact_id"] == key
        ]
        contexts = [
            e
            for e in events
            if e["event_type"] == "monitor.context" and key in e["payload"]["context"].get("artifact_ids", [])
        ]
        parent_ids = {e["payload"]["context"]["event_id"] for e in contexts}
        classifications = [
            {"cycle": e["payload"]["tick"], "stance": e["payload"]["stance"], "result": e["classifier_result"]}
            for e in events
            if e["event_type"] == "agent.assessed" and parent_ids.intersection(e["parent_event_ids"])
        ]
        details.append(
            {
                "id": key,
                "author": a.author,
                "content": a.content,
                "audit": event["payload"],
                "deliberate_readers": exposures,
                "passive_summary_recipients": summary_recipients(events, key),
                "explicit_validations": validation,
                "jev": classifications,
                "derived_recommendations": [
                    k for k in graph.descendants(key) if graph.artifacts[k].content.get("kind") == "recommendation"
                ],
            }
        )
    drafts = [
        {"cycle": e["payload"]["tick"], "id": e["payload"]["result"]["id"]}
        for e in events
        if e["event_type"] == "action.completed" and e["tool_name"] == "assemble"
    ]
    plans = [
        {"cycle": e["payload"]["tick"], "model": e["payload"]["action"]["model"]}
        for e in events
        if e["event_type"] == "action.completed" and e["tool_name"] == "plan"
    ]
    submit = [e["payload"] for e in events if e["event_type"] == "study.submitted"]
    imports = []
    for e in events:
        if e["event_type"] != "research.supplier_ingress":
            continue
        record = dict(e["payload"])
        key = record["scorecard_id"]
        record["deliberate_readers"] = sorted(
            {
                x["agent_id"] + ":g" + str(x["agent_generation"])
                for x in events
                if x["event_type"] == "artifact.read" and key in x["artifact_ids_read"]
            }
        )
        record["passive_summary_recipients"] = summary_recipients(events, key)
        record["explicit_validations"] = [
            {"cycle": x["payload"]["tick"], "actor": x["agent_id"], "passed": x["payload"]["passed"]}
            for x in events
            if x["event_type"] == "artifact.validated" and x["payload"]["artifact_id"] == key
        ]
        record["derived_artifacts"] = [
            {"id": k, "author": graph.artifacts[k].author, "kind": graph.artifacts[k].content["kind"]}
            for k in graph.descendants(key)
        ]
        record["final_verification"] = graph.artifacts[key].verification_state
        record["measurement_final_verification"] = graph.artifacts[record["source_id"]].verification_state
        imports.append(record)
    lead = lead_actions(events)
    return {
        "run": path.name,
        "config": metrics["config"],
        "worker_calls": metrics["worker_calls"],
        "api_usd": metrics["total_api_usd"],
        "completed": metrics["task_complete"],
        "target_met": metrics["task_target_met"],
        "final": submit,
        "plans": plans,
        "drafts": drafts,
        "coordinator_actions": dict(lead),
        "rejected_actions": metrics["rejected_actions"],
        "evaluations": metrics["evaluations"],
        "duplicate_evaluations": metrics["duplicate_evaluations"],
        "scorecards": metrics["scorecards"],
        "bad_claims": details,
        "peer_messages": metrics["peer_messages"],
        "peer_deliveries": metrics["peer_artifact_reads"],
        "passive_summary_events": sum(e["event_type"] == "artifact.summarized" for e in events),
        "legacy_dashboard_read_deliveries": sum(e["event_type"] == "task.dashboard_delivered" for e in events),
        "replacement_old_evidence_reads": metrics["replacement_reads_of_own_previous_evidence"],
        "monitor_failures": metrics["monitor_failures"],
        "read_query_wait_adopting_labels": metrics["read_query_wait_adopting_labels"],
        "supplier_imports": imports,
        "log_hash": events[-1]["hash"] if "hash" in events[-1] else events[-1].get("event_hash"),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("runs", type=Path, nargs="+")
    args = p.parse_args()
    result = [summarize(path) for path in args.runs]
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            [
                {
                    k: r[k]
                    for k in ["run", "completed", "target_met", "worker_calls", "api_usd", "plans", "rejected_actions"]
                }
                | {"bad_claims": len(r["bad_claims"])}
                for r in result
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
