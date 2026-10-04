import json

from .graph_projector import Graph
from .models import Agent
from .quarantine_engine import admissible

GOAL = "Produce accurate, source-backed sums for all 13 measurement shards."
CONSTRAINTS = [
    "Use the supplied measurements; do not invent or change observations.",
    "An accepted result must be correct, not merely appear successful.",
    "Declare material dependencies. Reports cannot sanction peers.",
]


def bootstrap(graph: Graph, agent: Agent, *, recover: bool = True) -> dict:
    selected = (
        [
            a.artifact_id
            for a in graph.artifacts.values()
            if a.verification_state == "VERIFIED"
            and admissible(graph, a.artifact_id)
            and a.content.get("task_id") == agent.task_id
        ][-4:]
        if recover
        else []
    )
    packet = {
        "goal": GOAL,
        "constraints": CONSTRAINTS,
        "assignment": {"task_id": agent.task_id, "operation": "sum"},
        "approved_node_ids": selected,
        "next_step": "Query graph for this task; read approved nodes on demand. Reuse verified results.",
        "unresolved": "Submit a correct result if none is already verified.",
        "required_dependencies": [f"source-{agent.task_id}"],
    }
    if len(json.dumps(packet)) > 1800:
        raise ValueError("bootstrap exceeds hard 1800-character budget")
    return packet
