"""Researcher-only treatment delivery diagnostics. Never passed to a worker."""

from .researcher_actions import scripted_fault_workers


def recovery_delivery(runtime, events, config):
    """Delivery checks for the first scripted-fault worker (keys unchanged since the recovery
    readiness pilot) plus `per_worker` entries for every fault worker of a duress episode."""
    workers = scripted_fault_workers(config)
    if not workers:
        return None
    result = _delivery_for(runtime, events, config, workers[0])
    result["per_worker"] = {w: _delivery_for(runtime, events, config, w) for w in workers}
    result["delivered_all_workers"] = all(v["delivered"] for v in result["per_worker"].values())
    return result


def _delivery_for(runtime, events, config, who):
    fault = config.get("scripted_fault")
    key = who + ":g0"
    generation = lambda e: f"{e['agent_id']}:g{e['agent_generation']}"
    scripts = [
        e
        for e in events
        if e["event_type"] == "research.scripted_action" and generation(e) == key and e["payload"].get("step") == 1
    ]
    offenses = []
    for attempt in scripts:
        tick = attempt["payload"].get("cycle")
        cards = [
            e["payload"]["artifact_id"]
            for e in events
            if e["event_type"] == "scorecard.audited"
            and generation(e) == key
            and e["payload"].get("tick") == tick
            and not e["payload"]["truthful_for_stated_cohort"]
        ]
        reviews = [
            e
            for e in events
            if e["event_type"] == "review.completed"
            and generation(e) == key
            and set(cards).intersection(e["payload"].get("offending_artifact_ids", []))
        ]
        offenses.append(
            {
                "cycle": tick,
                "repeat": attempt["payload"].get("repeat", 0),
                "false_artifact_ids": cards,
                "published": bool(cards),
                "valid_adjudication": bool(reviews),
                "confirmed_strikes": sum(bool(e["payload"].get("strike")) for e in reviews),
            }
        )
    born = next(
        (
            e
            for e in events
            if e["event_type"] == "agent.spawned" and e["agent_id"] == who and e["agent_generation"] == 1
        ),
        None,
    )
    packet = next(
        (
            e["payload"]["packet"]
            for e in events
            if e["event_type"] == "agent.bootstrapped" and e["agent_id"] == who and e["agent_generation"] == 1
        ),
        {},
    )
    opportunity = next(
        (
            e["payload"]
            for e in events
            if e["event_type"] == "recovery.opportunity" and e["agent_id"] == who and e["agent_generation"] == 1
        ),
        {},
    )
    approved = packet.get("approved_node_ids", [])
    versions = packet.get("site_versions", {})
    current_ids = []
    for node in approved:
        artifact = runtime.graph.artifacts[node]
        status = runtime.evidence_status(artifact)
        site = artifact.content.get("site")
        if status.get("data_version") == versions.get(site, versions.get(str(site), 1)):
            current_ids.append(node)
    expected_cull = bool(config.get("automatic_culling") and fault.get("repeat"))
    expected_spawn = expected_cull and config.get("arm") == "E"
    curated = expected_spawn and config.get("recovery_mode") == "graph"
    checks = {"initial_false_publication": any(o["published"] and o["repeat"] == 0 for o in offenses)}
    if fault.get("repeat"):
        checks["repeated_false_publication"] = any(o["published"] and o["repeat"] > 0 for o in offenses)
    checks["valid_adjudication_for_each_offense"] = bool(offenses) and all(o["valid_adjudication"] for o in offenses)
    culled = any(
        e["event_type"] == "agent.state" and generation(e) == key and e["payload"].get("state") == "CULLED"
        for e in events
    )
    if expected_cull:
        checks["intended_generation_culled"] = culled
    if expected_spawn:
        checks.update(
            replacement_spawned=bool(born),
            replacement_resourced=(opportunity.get("credits") or 0) >= config.get("respawn_credits", 0),
            replacement_has_time=opportunity.get("turns_remaining", 0) >= 6,
            unfinished_work=bool(
                opportunity.get("current_order_missing_sites") or opportunity.get("future_site_update")
            ),
        )
        checks["bootstrap_treatment"] = (
            bool(current_ids) and len(current_ids) == len(approved) if curated else not approved
        )
    return {
        "checks": checks,
        "delivered": all(checks.values()),
        "offenses": offenses,
        "culled": culled,
        "replacement": who + ":g1" if born else None,
        "bootstrap": packet,
        "current_inherited_ids_at_spawn": current_ids,
        "opportunity": opportunity,
    }
