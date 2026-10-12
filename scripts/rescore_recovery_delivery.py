"""Re-score recovery delivery for finished episodes with the current `recovery_delivery` check.

Researcher-only, offline, no model calls. Episodes recorded before 2026-10-05 carry a
`bootstrap_treatment` computed by a check that failed every curated packet holding the
work order or an update notice. This rebuilds the task instance from the episode's frozen
config, replays the saved events into a graph, applies the recorded site updates and runs the
current check. The recorded metrics.json is never modified; the result is written beside it as
`recovery-delivery-correction.json` (or to --output) and summarized on stdout.

    python3 scripts/rescore_recovery_delivery.py private/runs/series7-duress-glm53/block-k1-s211-RG
    python3 scripts/rescore_recovery_delivery.py bundle.tar.gz --output docs/results/<series>/<name>.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bladegraph.event_log import EventLog  # noqa: E402
from bladegraph.graph_projector import Graph  # noqa: E402
from bladegraph.intervention_controller import Controller, Policy  # noqa: E402
from bladegraph.jev_classifier import FixtureClassifier  # noqa: E402
from bladegraph.operational import OperationalRuntime  # noqa: E402
from bladegraph.recovery_metrics import recovery_delivery  # noqa: E402
from bladegraph.reviewer import FixtureReviewer  # noqa: E402


def episode_dir(source: Path, workdir: Path) -> Path:
    if source.is_dir():
        return source
    with tarfile.open(source) as tar:
        members = [m for m in tar.getmembers() if ".." not in Path(m.name).parts]
        wanted = [m for m in members if Path(m.name).name in {"events.sqlite", "frozen-config.json", "metrics.json"}]
        if not wanted:
            raise ValueError(f"{source.name} holds no episode (events.sqlite, frozen-config.json, metrics.json)")
        tar.extractall(workdir, members=wanted)
    return workdir / Path(wanted[0].name).parent


def rebuild_runtime(config: dict, events: list):
    fleet = config.get("fleet") or {}
    rt = OperationalRuntime(
        EventLog(),
        Controller(FixtureClassifier(), FixtureReviewer(), Policy(), config.get("arm", "E")),
        seed=config["seed"],
        analysts=fleet.get("analysts", 8),
        reviewers=fleet.get("reviewers", 4),
    )
    rt.deadline = config["cycles"]
    rt.set_submission_cycle(config["submission_cycle"])
    task = config.get("task") or {}
    flip = "flip" if task.get("flip") else "hold"
    if task.get("update_cycles"):
        rt.configure_rolling(task["update_cycles"], task["decision_cycles"], first_sites=task.get("sites"), flip=flip)
    elif task.get("update_cycle"):
        rt.configure_two_stage(
            update_cycle=task["update_cycle"], final_cycle=task["final_cycle"], sites=task.get("sites"), flip=flip
        )
    if rt.two_stage and task.get("trap_label") and rt.two_stage["trap_label"] != task["trap_label"]:
        raise ValueError("instance reconstruction mismatch: trap label differs from the frozen config")
    rt.graph = Graph.replay(events)
    updates = [e for e in events if e["event_type"] == "site.updated"]
    for u in updates:
        if getattr(rt, "rounds", None):
            rt.rounds[u["payload"].get("round", 1) - 1]["applied"] = True
            rt.two_stage["applied"] = True
        for site, version in u["payload"]["versions"].items():
            rt.versions[int(site)] = version
    rt.tick = max((e.get("tick") or 0) for e in events) if events else 0
    return rt, len(updates)


def rescore(source: Path, output: Path | None, workdir: Path) -> dict:
    run = episode_dir(source, workdir)
    config = json.loads((run / "frozen-config.json").read_text())["config"]
    metrics = json.loads((run / "metrics.json").read_text())
    recorded = metrics.get("recovery_delivery")
    log = EventLog(run / "events.sqlite", logical_time=False)
    events = list(log.events())
    log.close()
    rt, updates = rebuild_runtime(config, events)
    new = recovery_delivery(rt, events, config)
    out = {
        "note": "Offline re-score with the current recovery_delivery check; the recorded metrics.json is unchanged.",
        "episode": str(source),
        "site_updates_replayed": updates,
        "recorded": (
            {
                "delivered": recorded.get("delivered"),
                "checks": recorded.get("checks"),
                "current_inherited_ids_at_spawn": recorded.get("current_inherited_ids_at_spawn"),
            }
            if recorded
            else None
        ),
        "corrected": (
            {
                "delivered": new["delivered"],
                "checks": new["checks"],
                "packet_evidence_ids": new["packet_evidence_ids"],
                "packet_extras": new["packet_extras"],
                "packet_unexpected_extras": new["packet_unexpected_extras"],
                "current_inherited_ids_at_spawn": new["current_inherited_ids_at_spawn"],
            }
            if new
            else None
        ),
    }
    if recorded and new:
        out["checks_changed"] = {
            k: [recorded["checks"].get(k), v] for k, v in new["checks"].items() if recorded["checks"].get(k) != v
        }
    target = output or (run / "recovery-delivery-correction.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", type=Path, nargs="+", help="episode run directory or evidence bundle (.tar.gz)")
    p.add_argument("--output", type=Path, default=None, help="write the correction here (single source only)")
    args = p.parse_args(argv)
    if args.output and len(args.source) > 1:
        p.error("--output takes a single source")
    with tempfile.TemporaryDirectory() as tmp:
        for i, source in enumerate(args.source):
            out = rescore(source, args.output, Path(tmp) / str(i))
            rec = (out["recorded"] or {}).get("delivered")
            cor = (out["corrected"] or {}).get("delivered")
            print(f"{source}: delivered {rec} -> {cor}; changed {out.get('checks_changed', {})}")


if __name__ == "__main__":
    main()
