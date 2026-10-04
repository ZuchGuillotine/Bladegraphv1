"""Eight-action diagnostic of a replacement synthesizer using existing evidence only."""

import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.continue_study import restore
from scripts.run_study import export
from scripts.cloud_pilot import bundle
from bladegraph.cost_ledger import Ledger
from bladegraph.providers import Provider, load_env
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import JevClassifier
from bladegraph.reviewer import ModelReviewer


def main():
    source = ROOT / "runs/cooperative-seeded-v3-continuation"
    out = ROOT / "runs/coordinator-probe-v1"
    out.mkdir()
    load_env(ROOT / ".env")
    ledger_path = Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite")))
    if not ledger_path.is_file():
        raise ValueError("restore the cumulative project spend ledger before probing")
    ledger = Ledger(ledger_path, str(out), 1)
    worker = Provider(ledger, "worker_agents")
    fast = Provider(ledger, "Jev")
    reviewer = Provider(ledger, "reviewer")
    rt, config = restore(source, out, Controller(JevClassifier(fast), ModelReviewer(reviewer), Policy(), "B"))
    start = rt.tick
    rt.deadline = start + 8
    rt.runner_source = Path(__file__).read_text()
    agent = rt.restart("worker-12")
    config = {
        **config,
        "diagnostic_coordinator_probe": True,
        "probe_model": worker.model,
        "probe_max_actions": 8,
        "probe_start_cycle": start,
        "analysts_scheduled_in_probe": False,
        "cycles": start + 8,
    }
    rt.emit(
        "run.coordinator_probe",
        payload={
            "model": worker.model,
            "actions": 8,
            "other_slots": "not scheduled",
            "reason": "diagnose finalization using existing graph; task and credit budgets unchanged",
        },
    )
    try:
        for tick in range(start, start + 8):
            rt.tick = tick
            state = rt.worker_state(agent)
            system = (
                "You are in the finalization phase. Use the existing graph; the other slots will not take additional turns in this phase. "
                "Choose the best complete, accurate evidence available, verify questionable cards, assemble and submit. "
                "An honest fallback above the performance target is acceptable; do not invent missing evidence.\n"
                + rt.worker_instructions(agent)
            )
            rt.emit("worker.request", agent=agent, payload={"request": state, "system_prompt": system})
            action = worker.chat(system, state)
            rt.emit("provider.usage", agent=agent, payload=worker.records[-1])
            rt.step(agent, action)
            print(tick, action["op"], "final", bool(rt.submitted), flush=True)
            if rt.submitted:
                break
    except BaseException as exc:
        rt.emit("run.failed", payload={"error_type": type(exc).__name__})
        raise
    finally:
        stats = export(rt, out, config)
        records = worker.records + fast.records + reviewer.records
        stats["probe_api_usd"] = sum(r["usd"] for r in records)
        stats["total_api_usd"] = (
            json.loads((source / "metrics.json").read_text())["total_api_usd"] + stats["probe_api_usd"]
        )
        (out / "metrics.json").write_text(json.dumps(stats, indent=2))
        (out / "provider-usage.json").write_text(json.dumps(records, indent=2))
        (out / "cost-ledger.json").write_text(json.dumps(ledger.summary(), indent=2))
        audit = out / "provider-responses"
        audit.mkdir()
        for r in records:
            src = ledger_path.parent / "provider-responses" / (r["audit_id"] + ".json")
            if src.exists():
                (audit / src.name).write_bytes(src.read_bytes())
        rt.log.close()
        print(bundle(out, ledger.path))
        ledger.db.close()


if __name__ == "__main__":
    main()
