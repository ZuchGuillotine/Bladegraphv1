"""Researcher-only path review of one generation or artifact from a retained run. Offline; no API calls."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.event_log import EventLog
from bladegraph.graph_projector import Graph
from bladegraph.path_review import agent_path, artifact_lineage


def load(run):
    if (run / "events.sqlite").exists():
        log = EventLog(run / "events.sqlite")
        log.verify()
        events = list(log.events())
        log.close()
    else:
        events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    return events, Graph.replay(events)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path, help="run directory containing events.sqlite or events.jsonl")
    p.add_argument("--agent", help="generation key such as worker-04:g0")
    p.add_argument("--artifact", help="artifact ID to trace")
    p.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = p.parse_args()
    if not args.agent and not args.artifact:
        p.error("choose --agent and/or --artifact")
    events, graph = load(args.run)
    result = {"run": args.run.name, "note": "Researcher evidence only; never deliver to participants."}
    if args.agent:
        result["agent_path"] = agent_path(events, graph, args.agent)
    if args.artifact:
        result["artifact_lineage"] = artifact_lineage(events, graph, args.artifact)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
        print(str(args.output))
    else:
        print(text)


if __name__ == "__main__":
    main()
