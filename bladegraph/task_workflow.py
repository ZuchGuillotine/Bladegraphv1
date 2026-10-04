"""Task action suggestions from a public DTO only; no runtime or research configuration."""


def task_options(view):
    role = view["role"]
    rows = view["candidates"]
    order = view["selected_model"]
    cards = view["site_evidence"]
    reads = set(view["read_ids"])
    credits = view["credits"]
    tick = view["cycle"]

    def measured(model, site):
        options = []
        current = [
            c
            for c in cards
            if c["model"] == model and c["site"] == site and c["cohort"] == "all" and c.get("current", True)
        ]
        claims = [c for c in current if c["kind"] == "scorecard"]
        if claims:
            unverified = [c["id"] for c in claims if c["verification"] != "VERIFIED"]
            return [{"op": "verify_many", "artifact_ids": unverified[-1:]}] if unverified and role == "reviewer" else []
        evaluations = [c for c in current if c["kind"] == "evaluation"]
        if evaluations:
            if not can_publish:
                return []  # measurement exists; publishing waits for standing to return
            key = evaluations[-1]["id"]
            return (
                [{"op": "publish_evaluation", "artifact_id": key}]
                if key in reads
                else [{"op": "read", "artifact_id": key}]
            )
        if credits >= 2:
            options.append({"op": "evaluate", "model": model, "site": site, "cohort": "all"})
        return options

    can_publish = view.get("can_publish", True)
    if view.get("unread_approved"):
        # Recovered generations must read their inherited nodes before citing them.
        return [{"op": "read", "artifact_id": view["unread_approved"][0]}]
    if role == "analyst":
        for model in dict.fromkeys(["m0", view["exploratory_model"], order]):
            if model:
                options = measured(model, view["site"])
                if options:
                    return options
    elif role == "reviewer":
        for model in dict.fromkeys([order, "m0"]):
            if model:
                for site in view["review_sites"]:
                    options = measured(model, site)
                    if options:
                        return options
    else:
        options = coordinator_options(view, reads, tick)
        if options and (can_publish or options[0]["op"] in {"read", "comparison", "verify_many"}):
            return options
    unread = view["inbox_node_ids"]
    # Every role always receives at least one valid option.
    return [{"op": "read", "artifact_id": unread[-1]}] if unread else [{"op": "wait"}]


def coordinator_options(view, reads, tick):
    window = view.get("submission_cycle", 14)
    # Two-stage runs say which submission the cycle accepts; older views open at the window.
    open_now = view.get("submission_open", tick >= window)
    rows = view["candidates"]
    order = view["selected_model"]
    for draft in reversed(view["drafts"]):
        if draft["available"] and draft["verified"] and draft.get("current", True) and open_now:
            return [{"op": "submit" if draft["id"] in reads else "read", "artifact_id": draft["id"]}]
    choices = sorted((r for r in rows if r["sites_present"]), key=lambda r: (r["reported_loss"], r["model"]))
    if order is None and tick >= 4 and choices:
        # Dashboard rows are passive summaries. A plan must cite a card the coordinator actually read,
        # so the first suggestion is a deliberate read of the best reported card.
        best = choices[0]["scorecard_ids"][0]
        if best not in reads:
            return [{"op": "read", "artifact_id": best}]
        return [
            {"op": "plan", "model": r["model"], "derived_from": [k for k in r["scorecard_ids"] if k in reads][:1]}
            for r in choices
            if any(k in reads for k in r["scorecard_ids"])
        ]
    full = [r for r in choices if len(r["sites_present"]) == 8]
    if open_now and full:
        row = full[0]
        needed = [k for k in row["scorecard_ids"] if view["card_verification"][k] != "VERIFIED"]
        if needed:
            return [{"op": "verify_many", "artifact_ids": needed}]
        return [{"op": "assemble", "artifact_ids": row["scorecard_ids"]}]
    if order:
        row = next((r for r in rows if r["model"] == order), None)
        if row and row["missing_sites"]:
            return [
                {
                    "op": "message",
                    "text": "Please complete full-population coverage for " + order + " at your assigned site.",
                    "to": ["worker-" + str(i).zfill(2) for i in row["missing_sites"]],
                    "derived_from": [k for k in row["scorecard_ids"] if k in reads][:1],
                }
            ]
    if choices:
        return [{"op": "comparison"}]
    return []
