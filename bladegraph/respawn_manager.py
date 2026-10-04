from .bootstrap_builder import bootstrap
from .models import Agent


def respawn(runtime, old: Agent, *, recover: bool) -> tuple[Agent, dict]:
    if old.state != "CULLED":
        raise ValueError("only terminated generations can respawn")
    runtime.emit(
        "agent.spawned",
        agent_id=old.agent_id,
        agent_generation=old.generation + 1,
        task_id=old.task_id,
        payload={"previous": old.key, "state": "RESPAWNED"},
    )
    fresh = runtime.graph.agents[f"{old.agent_id}:g{old.generation + 1}"]
    # The recovery mode is a property of the generation: every later worker request regenerates
    # the packet, so a blank control must stay blank (2026-09-28 review finding).
    runtime.bootstrap_modes[fresh.key] = recover
    if hasattr(runtime, "grant_respawn_credits"):
        runtime.grant_respawn_credits(fresh)  # before the packet, so it shows the allowance
    packet = runtime.bootstrap(fresh, recover=recover)
    runtime.emit("agent.bootstrapped", agent=fresh, payload={"packet": packet})
    if hasattr(runtime, "comparison_rows"):
        order = runtime.work_order() if hasattr(runtime, "work_order") else None
        selected = order.content.get("selected_model") if order else None
        coverage = next((r for r in runtime.comparison_rows() if r["model"] == selected), None)
        ts = getattr(runtime, "two_stage", None)
        runtime.emit(
            "recovery.opportunity",
            agent=fresh,
            payload={
                "turns_remaining": max(0, getattr(runtime, "deadline", 0) - runtime.tick - 1),
                "current_order_missing_sites": coverage.get("missing_sites", []) if coverage else None,
                "future_site_update": bool(ts and not ts["applied"]),
                "credits": packet.get("evaluation_credits_remaining"),
                "packet_characters": len(__import__("json").dumps(packet)),
            },
        )
    if packet.get("approved_nodes"):
        # Content delivered in the packet is exposure: recorded as reads, like any delivery.
        runtime.emit("artifact.read", agent=fresh, artifact_ids_read=[n["id"] for n in packet["approved_nodes"]])
    # New conversation, no raw old context, reads or private memory.
    runtime.contexts[fresh.key] = []
    if hasattr(runtime, "monitor_history"):
        runtime.monitor_history[fresh.key] = []
    return fresh, packet
