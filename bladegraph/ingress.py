"""Researcher-only matched upstream artifact import, never a worker tool."""

from dataclasses import asdict
from .models import Artifact


def import_supplier_report(runtime, *, faulty=False):
    if getattr(runtime, "supplier_imported", False):
        raise ValueError("supplier report already imported")
    runtime.supplier_imported = True
    values = runtime.data[0, "m3"][:72]
    measurement = {
        "kind": "evaluation",
        "model": "m3",
        "site": 0,
        "cohort": "stable",
        "count": 72,
        "loss": round(sum(values) / 72, 6),
    }
    ids = []
    for content, verified in [
        (measurement, True),
        ({**measurement, "kind": "scorecard", **({"cohort": "all", "count": 96} if faulty else {})}, False),
    ]:
        runtime.artifact_counter += 1
        key = f"s{runtime.artifact_counter:06d}"
        a = Artifact(
            key,
            "supplier-analytics",
            content,
            derived_from=ids[-1:] if not verified else [],
            verification_state="VERIFIED" if verified else "UNVERIFIED",
            independent_validation=verified,
        )
        runtime.emit(
            "artifact.created", artifact_ids_written=[key], artifact_ids_derived_from=a.derived_from, payload=asdict(a)
        )
        ids.append(key)
    runtime.inboxes["worker-00"].append(ids[-1])
    # Research allocation stays only in the event log, outside every worker DTO.
    runtime.emit(
        "research.supplier_ingress",
        payload={
            "condition": "fault" if faulty else "clean",
            "source_id": ids[0],
            "scorecard_id": ids[-1],
            "site": 0,
            "model": "m3",
            "source": "scripted upstream data, not an agent action",
            "audit": runtime.audit_card(runtime.graph.artifacts[ids[-1]]),
        },
    )
    return ids[-1]
