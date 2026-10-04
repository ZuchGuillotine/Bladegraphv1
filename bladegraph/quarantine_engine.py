from .graph_projector import Graph

PLACEHOLDER = (
    "This route currently conflicts with a project constraint or has failed validation. "
    "Derivatives may be rejected. Use an alternative verified route or request review."
)


def quarantine(graph: Graph, emit, artifact_id: str, evidence: str) -> list[str]:
    ids = sorted({artifact_id} | graph.descendants(artifact_id, stop_at_verified=True))
    emit("artifact.quarantined", parent_event_ids=[evidence], payload={"root": artifact_id, "artifact_ids": ids})
    return ids


def admissible(graph: Graph, artifact_id: str) -> bool:
    a = graph.artifacts[artifact_id]
    return a.quarantine_state == "CLEAR" and a.verification_state != "FAILED"


def release(graph: Graph, emit, artifact_id: str, evidence: str) -> list[str]:
    """Controller-only reversal after a reviewed false positive; never exposed as a worker tool."""
    a = graph.artifacts[artifact_id]
    if a.verification_state == "FAILED" or (
        not a.independent_validation and any(not admissible(graph, d) for d in a.derived_from)
    ):
        return []
    emit("artifact.cleared", parent_event_ids=[evidence], payload={"artifact_id": artifact_id})
    cleared = [artifact_id]
    # Artifact insertion order is topological, so parents clear before children.
    descendants = graph.descendants(artifact_id)
    for key, child in graph.artifacts.items():
        if key in descendants and child.quarantine_state == "REEVALUATE" and child.local_content_risk < 0.25:
            if child.verification_state != "FAILED" and all(admissible(graph, d) for d in child.derived_from):
                emit("artifact.cleared", parent_event_ids=[evidence], payload={"artifact_id": key})
                cleared.append(key)
    from .risk_engine import recompute

    recompute(graph)
    return cleared


def worker_artifact(graph: Graph, artifact_id: str, signal: bool = True) -> dict:
    if artifact_id not in graph.artifacts or not admissible(graph, artifact_id):
        return {"status": "unavailable", **({"notice": PLACEHOLDER} if signal else {})}
    a = graph.artifacts[artifact_id]
    # Explicit data transfer object; no risks, reviewer evidence, or metadata spread.
    return {
        "id": artifact_id,
        "content": a.content,
        "derived_from": a.derived_from,
        "verification": a.verification_state,
    }
