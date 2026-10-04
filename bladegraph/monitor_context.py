"""Collect structured, actually visible evidence IDs; never parse IDs from prose."""

REFERENCE_FIELDS = {
    "id",
    "artifact_id",
    "artifact_ids",
    "derived_from",
    "node_ids",
    "scorecard_ids",
    "approved_node_ids",
}


def visible_artifacts(value, known_ids):
    found = set()

    def visit(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in REFERENCE_FIELDS:
                    values = child if isinstance(child, list) else [child]
                    found.update(x for x in values if isinstance(x, str) and x in known_ids)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found
