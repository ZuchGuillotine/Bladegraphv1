"""Researcher-side reconstruction of one generation's path through a run.

Built from the immutable event log plus the replayed graph. It answers the review questions a
forced or monitor-driven intervention raises: what did this generation read, publish, claim and
receive; how was each artifact audited, classified and reviewed; what became of its lineage;
and what did its replacement inherit. Output is researcher evidence and never worker input.
"""

from __future__ import annotations

from .operational import DIRECTIVE_MARKERS


def agent_path(events, graph, agent_key: str) -> dict:
    agent_id, _, generation = agent_key.rpartition(":g")
    generation = int(generation)
    own = [e for e in events if e["agent_id"] == agent_id and e["agent_generation"] == generation]
    audits = {
        e["payload"]["artifact_id"]: e["payload"]
        for e in events
        if e["event_type"] in {"scorecard.audited", "dashboard.scored"}
    }
    authored = [key for key, a in graph.artifacts.items() if a.author == agent_key]
    path = {
        "agent": agent_key,
        "state": graph.agents[agent_key].state if agent_key in graph.agents else None,
        "spawned": next(
            ({"tick": e["payload"].get("tick"), **e["payload"]} for e in own if e["event_type"] == "agent.spawned"),
            None,
        ),
        "bootstrap": next((e["payload"]["packet"] for e in own if e["event_type"] == "agent.bootstrapped"), None),
        "private_directive_issued": any(
            e["event_type"] == "worker.request"
            and any(m in e["payload"].get("system_prompt", "") for m in DIRECTIVE_MARKERS)
            for e in own
        ),
        "actions": [
            {
                "tick": e["payload"].get("tick"),
                "event_id": e["event_id"],
                "status": e["event_type"].split(".")[1],
                "action": e["payload"]["action"],
                **({"result": e["payload"]["result"]} if e["event_type"] == "action.completed" else {}),
                **({"reason": e["payload"]["reason"]} if e["event_type"] == "action.rejected" else {}),
            }
            for e in own
            if e["event_type"] in {"action.completed", "action.rejected"}
        ],
        "deliberate_reads": [
            {"tick": e["payload"].get("tick"), "artifact_ids": e["artifact_ids_read"]}
            for e in own
            if e["event_type"] == "artifact.read"
        ],
        "passive_summaries": [
            {
                "tick": e["payload"].get("tick"),
                "channel": e["payload"]["channel"],
                "artifact_ids": e["payload"]["artifact_ids"],
            }
            for e in own
            if e["event_type"] == "artifact.summarized"
        ],
        "authored_artifacts": [artifact_record(graph, key, audits.get(key)) for key in authored],
        "classifications": [
            {
                "tick": e["payload"].get("tick"),
                "event_id": e["event_id"],
                "stance": e["payload"]["stance"],
                "risk": e["payload"]["risk"],
                "model": (e.get("classifier_result") or {}).get("model"),
            }
            for e in own
            if e["event_type"] == "agent.assessed"
        ],
        "reviews": [
            {"tick": e["payload"].get("tick"), **e["payload"]} for e in own if e["event_type"] == "review.completed"
        ],
        "reports_filed": [e["payload"] for e in own if e["event_type"] == "report.created"],
        "state_transitions": [
            {"tick": e["payload"].get("tick"), "event_id": e["event_id"], **e["payload"]}
            for e in own
            if e["event_type"] == "agent.state"
        ],
        "researcher_interventions": [
            {"tick": e["payload"].get("tick"), "event_id": e["event_id"], **e["payload"]}
            for e in events
            if e["event_type"] == "research.intervention"
            and (
                (e["agent_id"] == agent_id and e["agent_generation"] == generation)
                or e["payload"].get("artifact_id") in authored
            )
        ],
        "monitor_failures": sum(e["event_type"] == "monitor.failed" for e in own),
    }
    successor = f"{agent_id}:g{generation + 1}"
    if successor in graph.agents:
        path["replacement"] = {
            "agent": successor,
            "bootstrap": next(
                (
                    e["payload"]["packet"]
                    for e in events
                    if e["event_type"] == "agent.bootstrapped"
                    and e["agent_id"] == agent_id
                    and e["agent_generation"] == generation + 1
                ),
                None,
            ),
            "read_own_previous_evidence": sorted(key for key in graph.agents[successor].reads if key in authored),
        }
    return path


def artifact_record(graph, key: str, audit: dict | None) -> dict:
    a = graph.artifacts[key]
    return {
        "id": key,
        "kind": a.content.get("kind"),
        "content": a.content,
        "derived_from": a.derived_from,
        "verification_state": a.verification_state,
        "quarantine_state": a.quarantine_state,
        "effective_risk": a.effective_risk,
        "audit": audit,
        "deliberate_readers": graph.consumers(key),
        "descendants": [
            {
                "id": d,
                "author": graph.artifacts[d].author,
                "kind": graph.artifacts[d].content.get("kind"),
                "quarantine_state": graph.artifacts[d].quarantine_state,
            }
            for d in sorted(graph.descendants(key))
        ],
        "dependent_submissions": graph.dependent_submissions(key),
    }


def artifact_lineage(events, graph, artifact_id: str) -> dict:
    """Ancestors, descendants, readers, validations and interventions around one artifact."""
    ancestors, pending = [], list(graph.artifacts[artifact_id].derived_from)
    while pending:
        key = pending.pop()
        if key in ancestors:
            continue
        ancestors.append(key)
        pending.extend(graph.artifacts[key].derived_from)
    return {
        "artifact": artifact_record(graph, artifact_id, None),
        "ancestors": [artifact_record(graph, key, None) for key in ancestors],
        "validations": [
            {
                "tick": e["payload"].get("tick"),
                "actor": f"{e['agent_id']}:g{e['agent_generation']}",
                "passed": e["payload"]["passed"],
            }
            for e in events
            if e["event_type"] == "artifact.validated" and e["payload"]["artifact_id"] == artifact_id
        ],
        "quarantines": [
            {
                "tick": e["payload"].get("tick"),
                "event_id": e["event_id"],
                "root": e["payload"]["root"],
                "artifact_ids": e["payload"]["artifact_ids"],
            }
            for e in events
            if e["event_type"] == "artifact.quarantined" and artifact_id in e["payload"]["artifact_ids"]
        ],
        "interventions": [
            {"tick": e["payload"].get("tick"), **e["payload"]}
            for e in events
            if e["event_type"] == "research.intervention" and e["payload"].get("artifact_id") == artifact_id
        ],
        "passive_summary_recipients": sorted(
            {
                f"{e['agent_id']}:g{e['agent_generation']}"
                for e in events
                if e["event_type"] == "artifact.summarized" and artifact_id in e["payload"]["artifact_ids"]
            }
        ),
    }
