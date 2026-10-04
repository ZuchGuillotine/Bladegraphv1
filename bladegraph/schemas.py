"""Export machine-readable interface schemas for collaborators."""

import json
from pathlib import Path

from .event_log import EventLog
from .models import EDGE_TYPES, NODE_TYPES, Stance

PROBABILITY = {"type": "number", "minimum": 0, "maximum": 1}
CLASSIFIER = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stance_distribution": {
            "type": "object",
            "additionalProperties": False,
            "properties": {s.value: PROBABILITY for s in Stance},
            "required": [s.value for s in Stance],
        },
        **{
            key: PROBABILITY
            for key in (
                "goal_violation_probability",
                "action_enablement_probability",
                "transmission_probability",
                "reasoning_action_mismatch_probability",
                "artifact_risk_probability",
                "confidence",
            )
        },
        "requires_review": {"type": "boolean"},
        "model": {"type": "string"},
    },
}
CLASSIFIER["required"] = list(CLASSIFIER["properties"])


def schemas() -> dict:
    log = EventLog()
    event = log.append("example")
    log.close()
    fields = {}
    for key, value in event.items():
        fields[key] = (
            {"type": "string"}
            if isinstance(value, str)
            else (
                {"type": "integer", "minimum": 0}
                if type(value) is int
                else {"type": "array", "items": {"type": "string"}} if isinstance(value, list) else {"type": "object"}
            )
        )
    fields["tool_name"] = {"type": ["string", "null"]}
    fields["classifier_result"] = {"anyOf": [CLASSIFIER, {"type": "null"}]}
    event_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "BladeGraph event v1",
        "type": "object",
        "required": list(fields),
        "additionalProperties": False,
        "properties": fields,
    }
    node = {
        "type": "object",
        "required": ["id", "type"],
        "properties": {"id": {"type": "string"}, "type": {"enum": sorted(NODE_TYPES)}},
    }
    edge = {
        "type": "object",
        "required": ["source", "target", "type", "event_id", "timestamp"],
        "properties": {
            "type": {"enum": sorted(EDGE_TYPES)},
            **{k: {"type": "string"} for k in ("source", "target", "event_id", "timestamp")},
        },
    }
    graph = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "BladeGraph graph projection v1",
        "type": "object",
        "required": ["nodes", "edges", "agents", "artifacts"],
        "additionalProperties": False,
        "properties": {
            "nodes": {"type": "object", "additionalProperties": node},
            "edges": {"type": "array", "items": edge},
            "agents": {"type": "object"},
            "artifacts": {"type": "object"},
        },
    }
    return {
        "event": event_schema,
        "graph": graph,
        "classification": {"$schema": "https://json-schema.org/draft/2020-12/schema", **CLASSIFIER},
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "schemas"
    root.mkdir(exist_ok=True)
    for name, value in schemas().items():
        (root / (name + ".schema.json")).write_text(json.dumps(value, indent=2) + "\n")
