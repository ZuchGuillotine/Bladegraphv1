"""Explicit diagnostic continuation: preserve original traces, budgets, identities and failures."""

import argparse, json, os, sys, sqlite3
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.cooperative import CooperativeRuntime
from bladegraph.event_log import EventLog
from bladegraph.graph_projector import Graph
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import JevClassifier
from bladegraph.providers import Provider, load_env
from bladegraph.reviewer import ModelReviewer
from bladegraph.cost_ledger import Ledger
from scripts.run_study import export
from scripts.cloud_pilot import bundle


def restore(source, out, controller):
    config = json.loads((source / "metrics.json").read_text())["config"]
    source_log = EventLog(source / "events.sqlite")
    source_log.verify()
    with closing(sqlite3.connect(out / "events.sqlite")) as dst:
        source_log.db.backup(dst)
    source_log.close()
    temporary = EventLog()
    rt = CooperativeRuntime(temporary, controller, config["seed"], config["condition"] == "seeded")
    temporary.close()
    rt.log = EventLog(out / "events.sqlite", logical_time=False)
    events = list(rt.log.events())
    rt.graph = Graph.replay(events)
    for e in events:
        kind = e["event_type"]
        p = e["payload"]
        who = e["agent_id"]
        key = who + ":g" + str(e["agent_generation"])
        if kind == "agent.spawned":
            rt.contexts[key] = []
            rt.monitor_history[key] = []
        elif kind == "action.completed":
            rt.contexts[key].append({"op": e["tool_name"], "result": p["result"]})
            rt.monitor_history[key].append({"event_id": e["event_id"], "action": p["action"], "result": p["result"]})
        elif kind == "action.rejected":
            rt.contexts[key].append({"op": "rejected", "result": {"status": "unavailable", "notice": p["reason"]}})
        elif kind == "evaluation.charged":
            rt.credits[who] -= p["credits"]
        elif kind == "allocation.granted":
            for who in p["winners"]:
                rt.credits[who] += p["extra_credits_each"]
            rt.grants_done = True
        elif kind == "artifact.created":
            if p["artifact_id"].startswith("s") and p["artifact_id"][1:].isdigit():
                rt.artifact_counter = max(rt.artifact_counter, int(p["artifact_id"][1:]))
            if p["content"]["kind"] == "note":
                for recipient in e["message_recipients"]:
                    rt.inboxes[recipient.split(":")[0]].append(p["artifact_id"])
        elif kind == "study.submitted":
            rt.submitted = {k: p[k] for k in ["accepted", "reported_loss", "dashboard_target_met"]}
    rt.tick = max(e["payload"].get("tick", 0) for e in events) + 1
    return rt, config


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("choose a fresh diagnostic continuation directory")
    args.output = args.output.resolve()
    args.source = args.source.resolve()
    args.output.mkdir(parents=True)
    load_env(ROOT / ".env")
    ledger_path = Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite")))
    if not ledger_path.is_file():
        p.error("restore the cumulative project spend ledger before continuing")
    ledger = Ledger(ledger_path, str(args.output), 1)
    worker = Provider(ledger, "worker_agents")
    fast = Provider(ledger, "Jev")
    reviewer = Provider(ledger, "reviewer")
    rt, config = restore(
        args.source, args.output, Controller(JevClassifier(fast), ModelReviewer(reviewer), Policy(), "B")
    )
    start = rt.tick
    rt.deadline = start + 4
    rt.runner_source = Path(__file__).read_text()
    config = {
        **config,
        "diagnostic_continuation_of": args.source.name,
        "original_cycle_limit": config["cycles"],
        "extra_cycles": 4,
        "cycles": start + 4,
    }
    rt.emit(
        "run.continued",
        payload={
            "source": args.source.name,
            "reason": "comparison tool alias repair; original deadline failure retained",
            "additional_cycles": 4,
            "source_hashes": rt.implementation_hashes,
        },
    )
    try:
        for tick in range(start, start + 4):
            rt.tick = tick
            for a in rt.active_agents():
                state = rt.worker_state(a)
                system = rt.worker_instructions(a)
                rt.emit("worker.request", agent=a, payload={"request": state, "system_prompt": system})
                action = worker.chat(system, state)
                rt.emit("provider.usage", agent=a, payload=worker.records[-1])
                rt.step(a, action)
            print("continuation cycle", tick, "final", bool(rt.submitted), flush=True)
            if rt.submitted:
                break
    except BaseException as exc:
        rt.emit("run.failed", payload={"error_type": type(exc).__name__})
        raise
    finally:
        stats = export(rt, args.output, config)
        records = worker.records + fast.records + reviewer.records
        stats["additional_segment_api_usd"] = sum(r["usd"] for r in records)
        stats["original_segment_api_usd"] = json.loads((args.source / "metrics.json").read_text())["total_api_usd"]
        stats["total_api_usd"] = stats["additional_segment_api_usd"] + stats["original_segment_api_usd"]
        (args.output / "metrics.json").write_text(json.dumps(stats, indent=2))
        (args.output / "provider-usage.json").write_text(json.dumps(records, indent=2))
        (args.output / "cost-ledger.json").write_text(json.dumps(ledger.summary(), indent=2))
        audit = args.output / "provider-responses"
        audit.mkdir()
        for r in records:
            src = ledger_path.parent / "provider-responses" / (r["audit_id"] + ".json")
            if src.exists():
                (audit / src.name).write_bytes(src.read_bytes())
        rt.log.close()
        print("bundle", bundle(args.output, ledger_path), flush=True)
        ledger.db.close()


if __name__ == "__main__":
    main()
