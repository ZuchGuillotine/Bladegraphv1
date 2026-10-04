"""Render a retained run as plain-text Markdown for human review. Researcher-only; offline.

One section per agent generation, in scheduler order: each turn's action, its result or the
rejection reason, the monitor's stance and risk, artifacts created with their audit outcome,
and every intervention (monitor or researcher) that touched the generation. Worker system
prompts and raw provider responses stay in the evidence bundle; this is the readable path.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.event_log import EventLog


def load_events(run: Path) -> list[dict]:
    if (run / "events.sqlite").exists():
        log = EventLog(run / "events.sqlite")
        log.verify()
        events = list(log.events())
        log.close()
        return events
    return [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]


def brief(value, limit=220) -> str:
    text = json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render(events: list[dict], agents: list[str] | None = None) -> str:
    by_agent = defaultdict(list)
    assessed = {}
    audits = {}
    for e in events:
        if e["event_type"] == "agent.assessed":
            assessed[e["parent_event_ids"][0]] = e["payload"]
        if e["event_type"] in {"scorecard.audited", "dashboard.scored"}:
            audits[e["payload"]["artifact_id"]] = e["payload"]
    for e in events:
        key = f"{e['agent_id']}:g{e['agent_generation']}"
        if e["agent_id"] != "controller":
            by_agent[key].append(e)
    research = [e for e in events if e["event_type"] in {"research.intervention", "research.intervention_skipped"}]
    lines = ["# Run transcript", ""]
    lines.append(f"Events: {len(events)}. Generations: {len(by_agent)}. Researcher interventions: {len(research)}.")
    lines.append("")
    for key in sorted(by_agent):
        if agents and key not in agents:
            continue
        lines.append(f"## {key}")
        lines.append("")
        for e in by_agent[key]:
            kind = e["event_type"]
            p = e["payload"]
            tick = p.get("tick", "-")
            if kind == "agent.bootstrapped":
                lines.append(
                    f"- t{tick} bootstrap: approved {p['packet'].get('approved_node_ids')} ({len(json.dumps(p['packet']))} chars)"
                )
            elif kind == "action.completed":
                verdict = assessed.get(e["event_id"])
                stance = f" | monitor {verdict['stance']} risk {verdict['risk']}" if verdict else ""
                lines.append(f"- t{tick} {brief(p['action'], 160)} -> {brief(p['result'], 160)}{stance}")
            elif kind == "action.rejected":
                lines.append(f"- t{tick} REJECTED {brief(p['action'], 140)} | {p['reason']}")
            elif kind == "artifact.created" and e["agent_id"].startswith("worker"):
                audit = audits.get(p["artifact_id"])
                verdict = ""
                if audit is not None:
                    verdict = " | audit " + (
                        "truthful" if audit.get("truthful_for_stated_cohort", audit.get("audit_correct")) else "FALSE"
                    )
                lines.append(
                    f"- t{tick} created {p['artifact_id']} {brief(p['content'], 120)} from {p.get('derived_from', [])}{verdict}"
                )
            elif kind == "agent.state":
                source = p.get("source", "monitor or scheduler")
                lines.append(f"- t{tick} STATE {p['state']} ({p.get('reason', '')}; {source})")
            elif kind == "review.completed":
                lines.append(
                    f"- t{tick} reviewer {p.get('review_decision')} {p.get('stance')} -> {p.get('recommended_intervention')} ({p.get('confidence')})"
                )
            elif kind == "monitor.failed":
                lines.append(f"- t{tick} MONITOR FAILED {p.get('error_type')}")
        lines.append("")
    if research:
        lines.append("## Researcher interventions")
        lines.append("")
        for e in research:
            lines.append(f"- t{e['payload'].get('tick', '-')} {e['event_type']} {brief(e['payload'], 200)}")
        lines.append("")
    quarantines = [e for e in events if e["event_type"] == "artifact.quarantined"]
    if quarantines:
        lines.append("## Quarantines")
        lines.append("")
        for e in quarantines:
            lines.append(
                f"- t{e['payload'].get('tick', '-')} root {e['payload']['root']} -> {e['payload']['artifact_ids']}"
            )
        lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    p.add_argument("--agent", action="append", help="limit to these generation keys, e.g. worker-04:g0")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    text = render(load_events(args.run), args.agent)
    if args.output:
        args.output.write_text(text + "\n")
        print(str(args.output))
    else:
        print(text)


if __name__ == "__main__":
    main()
