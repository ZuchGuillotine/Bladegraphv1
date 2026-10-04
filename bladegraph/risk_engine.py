from .graph_projector import Graph


def recompute(graph: Graph) -> None:
    """Immutable creation order is topological. READ/CONTRADICTS never carry risk."""
    for artifact in graph.artifacts.values():
        inherited = max((graph.artifacts[d].effective_risk for d in artifact.derived_from), default=0.0)
        artifact.provenance_risk = 0.0 if artifact.independent_validation else inherited
        artifact.effective_risk = max(artifact.local_content_risk, artifact.provenance_risk)
