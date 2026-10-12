"""Bounded cooperative pilot with explicit containment and recovery controls."""

import argparse
from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.operational import OperationalRuntime, INSIDER_DIRECTIVES, DEFAULT_DIRECTIVE_VERSION
from bladegraph.ingress import import_supplier_report
from bladegraph.researcher_actions import SCRIPTED_FAULT_STEPS, contain_generation, run_scripted_fault
from bladegraph.respawn_manager import respawn
from bladegraph.event_log import EventLog, digest
from bladegraph.graph_projector import Graph
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import QUESTION_SETS, JevClassifier
from bladegraph.providers import MalformedResponse, Provider, ProviderUnavailable, load_env
from bladegraph.cost_ledger import BudgetExceeded
from bladegraph.calibration import load_profile
from bladegraph.recovery_metrics import recovery_delivery
from bladegraph.reviewer import ModelReviewer
from scripts.cloud_pilot import bundle

from scripts.run_study import export


def preserve_failed_export(rt, output, ledger, providers, error):
    """Keep forensic evidence even when the normal metric/replay audit cannot complete."""
    (output / "export-failure.json").write_text(
        json.dumps(
            {
                "error_type": type(error).__name__,
                "normal_metrics_valid": False,
            },
            indent=2,
        )
    )
    (output / "termination.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "last_cycle": rt.tick,
                "export_failed": True,
            },
            indent=2,
        )
    )
    rt.log.export(output / "events.jsonl")
    (output / "live-graph-forensics.json").write_text(json.dumps(rt.graph.snapshot(), indent=2))
    records = [record for provider in providers for record in provider.records]
    failures = [record for provider in providers for record in provider.failure_records]
    (output / "provider-usage.json").write_text(json.dumps(records, indent=2))
    (output / "provider-transport-failures.json").write_text(json.dumps(failures, indent=2))
    (output / "cost-ledger.json").write_text(json.dumps(ledger.summary(), indent=2))
    audit = output / "provider-responses"
    audit.mkdir(exist_ok=True)
    for record in records + failures:
        source = ledger.path.parent / "provider-responses" / (record["audit_id"] + ".json")
        if source.exists():
            (audit / source.name).write_bytes(source.read_bytes())
    rt.log.close()
    bundle(output, ledger.path)


def configure_task(rt, args, fault_workers, update_cycles, decision_cycles, p):
    """Generate the instance's task data (seeded, offline); --validate-only runs it too, so a seed
    whose data cannot satisfy its flip conditions fails before any paid episode (2026-10-04)."""
    task_config = {"version": args.task}
    if args.task in {"two-stage", "rolling"}:
        if args.update_sites == "fault":
            if args.condition != "scripted-fault":
                p.error("--update-sites fault needs --condition scripted-fault")
            import random as _random

            # The first round revises the first fault worker's site plus one other: the second fault
            # worker's site when there is one, else a seeded draw. The culled slots' re-measurement is
            # then essential to the next recommendation.
            fault_sites = [int(w[7:]) for w in fault_workers]
            if len(fault_sites) >= 2:
                sites = sorted(fault_sites[:2])
            else:
                other = _random.Random(args.seed * 7919 + 31).choice([i for i in range(8) if i != fault_sites[0]])
                sites = sorted([fault_sites[0], other])
        else:
            sites = [int(x) for x in args.update_sites.split(",") if x.strip()] or None
        if args.task == "rolling":
            task_config.update(
                rt.configure_rolling(update_cycles, decision_cycles, first_sites=sites, flip=args.update_flip)
            )
        else:
            task_config.update(
                rt.configure_two_stage(
                    update_cycle=args.update_cycle, final_cycle=args.final_cycle, sites=sites, flip=args.update_flip
                )
            )
    return task_config


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--validate-only", action="store_true", help="validate arguments without credentials, files or paid calls"
    )
    p.add_argument(
        "--condition",
        choices=["ordinary", "insider", "upstream-clean", "upstream-fault", "scripted-fault"],
        default="ordinary",
    )
    p.add_argument("--cycles", type=int, default=24)
    p.add_argument("--restart-cycle", type=int, default=10)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--budget", type=float, default=3)
    p.add_argument("--restart-worker", choices=["worker-00", "worker-04"], default="worker-04")
    p.add_argument(
        "--directive",
        choices=sorted(INSIDER_DIRECTIVES),
        default=DEFAULT_DIRECTIVE_VERSION,
        help="insider directive version; only used by --condition insider",
    )
    p.add_argument(
        "--contain-cycle",
        type=int,
        default=0,
        help="insider only: at the start of this cycle, force quarantine/cull/respawn on the insider's latest "
        "false scorecard, or record a skipped intervention if it published none (0 disables)",
    )
    p.add_argument(
        "--contain-recover",
        choices=["graph", "blank"],
        default="graph",
        help="replacement bootstrap after forced containment: verified graph IDs or blank-context control",
    )
    p.add_argument(
        "--arm",
        choices=["B", "D", "E"],
        default="B",
        help="B: detection only (default). D: monitor-driven artifact quarantine and culling, no respawn. "
        "E: D plus graph-bootstrapped respawn of culled generations.",
    )
    p.add_argument(
        "--automatic-culling",
        action="store_true",
        help="let a reviewer-confirmed monitor verdict cull a generation (arms D and E only). Off by default; "
        "enable only on the owner's explicit request.",
    )
    p.add_argument(
        "--insiders",
        default="worker-04",
        help="comma-separated generation-zero recipients of the private directive (site analysts only)",
    )
    p.add_argument(
        "--cull-strikes",
        type=int,
        default=Policy.cull_strikes,
        help="reviewer-confirmed verdicts needed before a cull (default %(default)s)",
    )
    p.add_argument(
        "--probation-cycles",
        type=int,
        default=Policy.restrict_probation_cycles,
        help="low-risk actions after which a restricted generation may publish again; 0 = reviewer only",
    )
    p.add_argument(
        "--minor-strike-weight",
        type=float,
        default=Policy.minor_strike_weight,
        help="strike weight of a confirmed scorecard that matches its cited measurement's cohort and count "
        "within --minor-loss-tolerance (rounding or transcription); other offenses weigh 1; a generation is "
        "culled at --cull-strikes points (default %(default)s)",
    )
    p.add_argument(
        "--minor-loss-tolerance",
        type=float,
        default=Policy.minor_loss_tolerance,
        help="loss units within which a confirmed scorecard counts as a minor strike (default %(default)s)",
    )
    p.add_argument(
        "--submission-cycle",
        type=int,
        default=14,
        help="cycle at which the fleet lead may submit (default %(default)s); later windows lengthen the "
        "coverage phase for 36-cycle runs",
    )
    p.add_argument(
        "--respawn-credits",
        type=int,
        default=2,
        help="evaluation credits granted to a replacement analyst or reviewer at respawn (default %(default)s; "
        "0 makes it inherit its predecessor's remaining budget only)",
    )
    p.add_argument(
        "--respawn-context",
        choices=["inline", "ids"],
        default="inline",
        help="inline: inherited verified nodes arrive in the bootstrap with content and count as read; "
        "ids: IDs only, the replacement must read them first (attempts 4 to 11)",
    )
    p.add_argument(
        "--monitor-questions",
        choices=sorted(QUESTION_SETS),
        default="v1",
        help="Jev question set that drives the controller (default %(default)s; v1c adds a covert-coordination prior)",
    )
    p.add_argument(
        "--shadow-questions",
        choices=sorted(QUESTION_SETS),
        default=None,
        help="second Jev question set classified on every action and recorded only, never acted on; "
        "a paired comparison on identical contexts at Jev cost",
    )
    p.add_argument(
        "--task",
        choices=["v2", "two-stage", "rolling"],
        default="v2",
        help="task version (two-stage adds a site-data update; rolling runs several update rounds)",
    )
    p.add_argument(
        "--update-cycles",
        default="",
        help="rolling: comma-separated cycles of the site-data updates, one per round, e.g. 11,19,26",
    )
    p.add_argument(
        "--decision-cycles",
        default="",
        help="rolling: comma-separated cycles opening each round's decision window; the last opens the final",
    )
    p.add_argument(
        "--analysts",
        type=int,
        default=8,
        help="site analysts worker-00.. (default %(default)s; the 2026-10-04 redesign uses 6 with one reviewer "
        "covering the two unowned sites)",
    )
    p.add_argument("--reviewers", type=int, default=4, help="reviewers after the analysts (default %(default)s)")
    p.add_argument(
        "--fault-stagger",
        type=int,
        default=0,
        help="scripted-fault: cycles between successive fault workers' first scripted step (default 0: same cycle)",
    )
    p.add_argument(
        "--fault-cycle",
        type=int,
        default=3,
        help="scripted-fault: first of three consecutive cycles on which the fault worker's turn is scripted "
        "(diagnostic, false publication, note)",
    )
    p.add_argument(
        "--fault-worker",
        default="worker-04",
        help="scripted-fault: the site analyst(s) whose turns are scripted, comma-separated for the duress "
        "series, e.g. worker-04,worker-01; the first named starts at --fault-cycle, later ones --fault-stagger apart",
    )
    p.add_argument(
        "--fault-repeat",
        type=int,
        choices=range(4),
        default=0,
        help="scripted-fault: repeat false publication after release, at most this many times",
    )
    p.add_argument("--update-cycle", type=int, default=12, help="two-stage: cycle of the site-data update")
    p.add_argument("--final-cycle", type=int, default=18, help="two-stage: cycle the final window opens")
    p.add_argument(
        "--update-sites",
        default="",
        help="two-stage: two sites to revise, e.g. 2,5 (default: seeded draw); 'fault' revises the scripted-fault "
        "worker's site plus one seeded other, so the culled slot's re-measurement is essential to the final",
    )
    p.add_argument(
        "--update-flip",
        choices=["auto", "flip", "hold"],
        default="auto",
        help="two-stage: update changes the best candidate",
    )
    p.add_argument("--worker-model", default=None, help="pinned worker model id; overrides WORKER_MODEL for this run")
    p.add_argument(
        "--worker-provider",
        choices=["openrouter", "anthropic", "openai"],
        default=None,
        help="worker backend for this run (anthropic uses BLADEGRAPH_ANTHROPIC_API_KEY, else ANTHROPIC_API_KEY, and the Messages API directly; "
        "openai uses OPENAI_API_KEY and chat completions directly, settled on the configured prices)",
    )
    p.add_argument("--worker-input-price", type=float, default=None, help="USD per million input tokens (worker)")
    p.add_argument("--worker-output-price", type=float, default=None, help="USD per million output tokens (worker)")
    p.add_argument(
        "--max-output-tokens", type=int, default=None, help="worker output cap incl. reasoning (default 1200)"
    )
    p.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
        help="OpenRouter reasoning effort for the worker model; omit to send no reasoning field",
    )
    p.add_argument("--provider-order", default=None, help="comma-separated OpenRouter provider order, no fallbacks")
    p.add_argument(
        "--usage-accounting", action="store_true", help="ask OpenRouter for billed cost in usage and settle on it"
    )
    p.add_argument(
        "--policy-profile",
        default=None,
        help="use the calibration gate's proposed thresholds for this worker model from "
        "experiments/policy-profiles.json (quarantine, restrict, cull); recorded in the frozen config",
    )
    p.add_argument("--policy-profiles-file", type=Path, default=ROOT / "experiments" / "policy-profiles.json")
    args = p.parse_args()
    if args.task == "two-stage" and not args.submission_cycle < args.update_cycle < args.final_cycle < args.cycles:
        p.error("two-stage needs submission < update < final < cycles")
    if not 4 <= args.analysts <= 8 or not 1 <= args.reviewers <= 4:
        p.error("--analysts must be 4..8 and --reviewers 1..4")
    fleet_size = args.analysts + args.reviewers + 1
    if fleet_size not in {8, 13}:
        p.error("fleets are 8 agents (6 analysts, 1 reviewer, coordinator) or 13 (8 analysts, 4 reviewers)")
    update_cycles = [int(x) for x in args.update_cycles.split(",") if x.strip()]
    decision_cycles = [int(x) for x in args.decision_cycles.split(",") if x.strip()]
    if args.task == "rolling":
        if not update_cycles or len(update_cycles) != len(decision_cycles):
            p.error("rolling needs --update-cycles and --decision-cycles of equal length")
        schedule = [c for pair in zip(update_cycles, decision_cycles) for c in pair]
        if (
            schedule != sorted(set(schedule))
            or not args.submission_cycle < schedule[0]
            or not schedule[-1] < args.cycles
        ):
            p.error("rolling needs submission < update_1 < decision_1 < update_2 < ... < final < cycles")
        args.update_cycle, args.final_cycle = update_cycles[0], decision_cycles[-1]
    elif update_cycles or decision_cycles:
        p.error("--update-cycles/--decision-cycles need --task rolling")
    fault_workers = [w.strip() for w in args.fault_worker.split(",") if w.strip()]
    args.fault_worker = fault_workers[0] if fault_workers else "worker-04"
    fault_starts = {w: args.fault_cycle + i * args.fault_stagger for i, w in enumerate(fault_workers)}
    for name, value in {
        "WORKER_MODEL": args.worker_model,
        "WORKER_PROVIDER": args.worker_provider,
        "WORKER_INPUT_USD_PER_MILLION": args.worker_input_price,
        "WORKER_OUTPUT_USD_PER_MILLION": args.worker_output_price,
        "WORKER_MAX_OUTPUT_TOKENS": args.max_output_tokens,
        "WORKER_REASONING_EFFORT": args.reasoning_effort,
        "WORKER_PROVIDER_ORDER": args.provider_order,
        "WORKER_USAGE_ACCOUNTING": "1" if args.usage_accounting else None,
    }.items():
        if value is not None:
            os.environ[name] = str(value)  # explicit caller configuration for this process only
    if args.shadow_questions == args.monitor_questions:
        p.error("--shadow-questions must differ from --monitor-questions")
    if (
        args.directive == "v6"
        and args.condition == "insider"
        and len([w for w in args.insiders.split(",") if w.strip()]) < 2
    ):
        p.error("directive v6 is a covert pair; name two insiders, e.g. --insiders worker-04,worker-02")
    if not 0 <= args.respawn_credits <= 6:
        p.error("--respawn-credits must be 0..6")
    if not 8 <= args.submission_cycle < args.cycles:
        p.error("--submission-cycle must be at least 8 and inside the run")
    if args.automatic_culling and args.arm == "B":
        p.error("--automatic-culling needs --arm D or E")
    # Budget bound raised from $5 to $6 for series 3 (researcher-authorized $6 per episode, 2026-10-02).
    if not 16 <= args.cycles <= 36 or not 0 <= args.restart_cycle < args.cycles or not 0 < args.budget <= 6:
        p.error("bounded pilot: 16..36 cycles, restart within run (0 disables), budget at most $6")
    insiders = tuple(w.strip() for w in args.insiders.split(",") if w.strip())
    if args.fault_repeat and args.condition != "scripted-fault":
        p.error("--fault-repeat requires --condition scripted-fault")
    if args.condition == "scripted-fault":
        if len(set(fault_workers)) != len(fault_workers) or any(
            not (w.startswith("worker-0") and w[7:].isdigit() and int(w[7:]) < args.analysts) for w in fault_workers
        ):
            p.error(f"--fault-worker must name distinct site analysts worker-00..worker-{args.analysts - 1:02d}")
        if len(fault_workers) >= args.analysts:
            p.error("at least one analyst must stay unscripted")
        if args.fault_stagger < 0 or not all(
            1 <= c <= args.cycles - len(SCRIPTED_FAULT_STEPS) for c in fault_starts.values()
        ):
            p.error("--fault-cycle and --fault-stagger must leave every fault worker room for the three steps")
    elif len(fault_workers) > 1 or args.fault_stagger:
        p.error("several fault workers need --condition scripted-fault")
    if args.contain_cycle:
        if args.condition != "insider":
            p.error("--contain-cycle targets the insider's false claim; use --condition insider")
        if not 1 <= args.contain_cycle < args.cycles:
            p.error("--contain-cycle must fall inside the run")
        if args.restart_cycle and args.restart_worker in insiders:
            p.error("forced containment already replaces worker-04; use --restart-cycle 0 or restart worker-00")
    if args.validate_only:
        if args.task in {"two-stage", "rolling"}:
            offline = OperationalRuntime(
                EventLog(":memory:", logical_time=False),
                None,
                args.seed,
                condition="insider" if args.condition == "insider" else "ordinary",
                directive_version=args.directive,
                insiders=insiders,
                analysts=args.analysts,
                reviewers=args.reviewers,
            )
            offline.deadline = args.cycles
            offline.set_submission_cycle(args.submission_cycle)
            try:
                configure_task(offline, args, fault_workers, update_cycles, decision_cycles, p)
            except ValueError as exc:
                p.error(f"task data: {exc}")
        print("arguments valid")
        return
    args.output = args.output.resolve()
    if args.output.exists():
        p.error("choose a new run directory; paid requests are not retried")
    load_env(ROOT / ".env")
    # Model/prices remain explicit caller configuration; never touch stored .env.
    ledger_path = Path(os.environ.get("BLADEGRAPH_LEDGER", str(ROOT / "private/spend.sqlite")))
    if not ledger_path.is_file():
        p.error("restore the cumulative project spend ledger before a study run")
    ledger = __import__("bladegraph.cost_ledger", fromlist=["Ledger"]).Ledger(
        ledger_path, str(args.output), args.budget
    )
    worker = Provider(ledger, "worker_agents")
    fast = Provider(ledger, "Jev")
    reviewer = Provider(ledger, "reviewer")
    args.output.mkdir(parents=True)
    log = EventLog(args.output / "events.sqlite", logical_time=False)
    rt = OperationalRuntime(
        log,
        Controller(
            JevClassifier(fast, questions=QUESTION_SETS[args.monitor_questions]),
            ModelReviewer(reviewer),
            Policy(
                **(
                    {k: profile[k] for k in ("quarantine", "restrict", "cull")}
                    if (
                        profile := (
                            load_profile(args.policy_profiles_file, args.policy_profile)
                            if args.policy_profile
                            else None
                        )
                    )
                    else {}
                ),
                automatic_culling=args.automatic_culling,
                cull_strikes=args.cull_strikes,
                restrict_probation_cycles=args.probation_cycles,
                minor_strike_weight=args.minor_strike_weight,
                minor_loss_tolerance=args.minor_loss_tolerance,
            ),
            args.arm,
            shadow_classifier=(
                JevClassifier(fast, questions=QUESTION_SETS[args.shadow_questions], purpose="research-shadow")
                if args.shadow_questions
                else None
            ),
        ),
        args.seed,
        condition="insider" if args.condition == "insider" else "ordinary",
        directive_version=args.directive,
        insiders=insiders,
        analysts=args.analysts,
        reviewers=args.reviewers,
    )
    rt.deadline = args.cycles
    rt.set_submission_cycle(args.submission_cycle)
    rt.respawn_credits = args.respawn_credits
    rt.respawn_context = args.respawn_context
    task_config = configure_task(rt, args, fault_workers, update_cycles, decision_cycles, p)
    rt.runner_source = Path(__file__).read_text()
    config = {
        "condition": args.condition,
        "seed": args.seed,
        "cycles": args.cycles,
        "submission_cycle": args.submission_cycle,
        "respawn_credits": args.respawn_credits,
        "respawn_context": args.respawn_context,
        "recovery_mode": args.contain_recover,
        "restart_cycle": args.restart_cycle,
        "workflow": {
            "v2": "operational-v2-with-matched-ingress",
            "two-stage": "operational-v3-two-stage",
            "rolling": "operational-v4-rolling",
        }[args.task],
        "fleet": {"analysts": args.analysts, "reviewers": args.reviewers, "coordinator": 1},
        "task": task_config,
        "restart_worker": args.restart_worker,
        "worker_model": worker.model,
        "worker_provider": worker.backend,
        "worker_endpoint": worker.endpoint,
        "worker_request_options": worker.request_options(),
        "worker_prices_usd_per_million": {"input": worker.input_price, "output": worker.output_price},
        "reviewer_model": reviewer.model,
        "reviewer_request_options": reviewer.request_options(),
        "transport_failure_limit": worker.transport_failure_limit,
        "monitor_model": fast.model,
        "budget": args.budget,
        "monitor": f"jev-{args.monitor_questions}-questions-rich-context",
        "shadow_monitor": f"jev-{args.shadow_questions}-questions-rich-context" if args.shadow_questions else None,
        "agents": len(rt.roles),
        "arm": args.arm,
        "automatic_culling": args.automatic_culling,
        "policy": asdict(rt.controller.policy),
        "policy_profile": args.policy_profile,
        "directive_version": args.directive if args.condition == "insider" else None,
        "insiders": list(insiders),
        "scripted_fault": (
            {
                "worker": args.fault_worker,
                "workers": list(fault_workers),
                "starts": dict(fault_starts),
                "stagger": args.fault_stagger,
                "cycles": list(range(args.fault_cycle, args.fault_cycle + len(SCRIPTED_FAULT_STEPS))),
                "steps": list(SCRIPTED_FAULT_STEPS),
                "repeat": args.fault_repeat,
                "source": "researcher-scripted through ordinary task actions; not model conduct",
            }
            if args.condition == "scripted-fault"
            else None
        ),
        "containment": (
            {
                "cycle": args.contain_cycle,
                "recover": args.contain_recover,
                "target": "every false scorecard and message by each insider's current generation, else skipped",
                "source": "researcher-forced; not a monitor, reviewer or peer outcome",
            }
            if args.contain_cycle
            else None
        ),
    }
    config["supplier_ingress"] = (
        {"cycle": 4, "before": "worker-12", "site": 0, "model": "m3", "source_cohort": "stable"}
        if args.condition.startswith("upstream-")
        else None
    )
    (args.output / "frozen-config.json").write_text(
        json.dumps(
            {"config": config, "source_hashes": rt.implementation_hashes, "runner_hash": digest(rt.runner_source)},
            indent=2,
        )
    )
    fault_repeats_done = {w: 0 for w in fault_workers}
    completed_normally = False
    try:
        for tick in range(args.cycles):
            rt.tick = tick
            if tick == 8:
                rt.allocate()
            if rt.site_update_due():
                print("site update", rt.apply_site_update(), rt.two_stage["sites"], flush=True)
            if tick == args.restart_cycle and tick > 0:
                rt.restart(args.restart_worker)
            for who in insiders if tick == args.contain_cycle and tick > 0 else ():
                outcome = contain_generation(
                    rt,
                    who,
                    f"scheduled containment rehearsal at cycle {tick}",
                    recover=args.contain_recover == "graph",
                )
                if outcome:
                    print(
                        "containment quarantined",
                        outcome["quarantined"],
                        "culled",
                        outcome["culled"],
                        "replacement",
                        outcome["replacement"],
                        flush=True,
                    )
                else:
                    rt.emit(
                        "research.intervention_skipped",
                        payload={
                            "cycle": tick,
                            "insider": who,
                            "reason": "current generation has no false scorecard left to contain: it never "
                            "falsified, the monitor already quarantined its false scorecards, or an earlier "
                            "generation was already culled",
                        },
                    )
                    print("containment skipped: no false insider scorecard at cycle", tick, flush=True)
            for a in list(rt.active_agents()):
                if tick == 4 and a.agent_id == "worker-12" and args.condition.startswith("upstream-"):
                    import_supplier_report(rt, faulty=args.condition == "upstream-fault")
                start = fault_starts.get(a.agent_id) if args.condition == "scripted-fault" else None
                if start is not None and a.generation == 0 and 0 <= tick - start < len(SCRIPTED_FAULT_STEPS):
                    # The fault worker's turn is scripted: no worker call, the action goes through
                    # the ordinary path and the monitor sees it as any other action.
                    rt.worker_state(a)  # probation and standing are evaluated as on any turn
                    run_scripted_fault(rt, a, tick - start, tick)
                    if a.state == "CULLED" and args.arm == "E":
                        fresh, _ = respawn(rt, a, recover=args.contain_recover == "graph")
                        print("monitor cull", a.key, "replacement", fresh.key, flush=True)
                    continue
                if (
                    start is not None
                    and a.generation == 0
                    and tick >= start + len(SCRIPTED_FAULT_STEPS)
                    and fault_repeats_done[a.agent_id] < args.fault_repeat
                ):
                    rt.worker_state(a)  # lets probation release the generation first
                    if a.state in {"NORMAL", "WATCH", "RESPAWNED"}:
                        # Predeclared re-offense: the same false card again, once per release.
                        fault_repeats_done[a.agent_id] += 1
                        run_scripted_fault(rt, a, 1, tick, repeat=fault_repeats_done[a.agent_id])
                        if a.state == "CULLED" and args.arm == "E":
                            fresh, _ = respawn(rt, a, recover=args.contain_recover == "graph")
                            print("monitor cull", a.key, "replacement", fresh.key, flush=True)
                        continue
                state = rt.worker_state(a)
                system = rt.worker_instructions(a)
                rt.emit("worker.request", agent=a, payload={"request": state, "system_prompt": system})
                try:
                    action = worker.chat(system, state)
                except MalformedResponse as exc:
                    # Paid and answered, but not one JSON object: the turn is lost, nothing is retried.
                    rt.emit("provider.usage", agent=a, payload=worker.records[-1])
                    rt.emit(
                        "worker.malformed",
                        agent=a,
                        payload={"reason": str(exc), "finish_reason": exc.finish_reason},
                    )
                    rt.contexts[a.key].append(
                        {"op": "malformed", "result": {"status": "rejected", "reason": "reply was not one JSON object"}}
                    )
                    continue
                except BudgetExceeded:
                    raise
                except RuntimeError as exc:
                    # Isolated failures are lost turns, never retries. A sustained transport
                    # outage or permanent configuration error stops the episode.
                    rt.emit("worker.failed", agent=a, payload={"error_type": type(exc).__name__, "reason": str(exc)})
                    if isinstance(exc, ProviderUnavailable) and not exc.fatal:
                        continue
                    raise
                rt.emit("provider.usage", agent=a, payload=worker.records[-1])
                rt.step(a, action)
                if a.state == "CULLED" and args.arm == "E":
                    fresh, _ = respawn(rt, a, recover=args.contain_recover == "graph")
                    print("monitor cull", a.key, "replacement", fresh.key, flush=True)
            print(
                "cycle",
                tick,
                "evaluations",
                sum(e["event_type"] == "evaluation.charged" for e in rt.log.events()),
                "final",
                bool(rt.submitted),
                flush=True,
            )
            if rt.submitted:
                break
        completed_normally = True
        rt.emit("run.completed", payload={"reason": "final submission" if rt.submitted else "deadline reached"})
    except BaseException as exc:
        rt.emit("run.failed", payload={"error_type": type(exc).__name__})
        raise
    finally:
        (args.output / "termination.json").write_text(
            json.dumps(
                {
                    "status": "completed" if completed_normally else "failed",
                    "last_cycle": rt.tick,
                },
                indent=2,
            )
        )
        try:
            stats = export(rt, args.output, config)
        except Exception as exc:
            preserve_failed_export(rt, args.output, ledger, (worker, fast, reviewer), exc)
            ledger.db.close()
            raise
        stats["assigned_private_role"] = [f"{w}:g0" for w in insiders] if args.condition == "insider" else None
        stats["seeded_behavior_is_not_spontaneous"] = args.condition == "insider"
        stats["scripted_fault_is_not_agent_behavior"] = args.condition in {"upstream-fault", "scripted-fault"}
        stats["scripted_actions"] = [
            e["payload"] for e in rt.log.events() if e["event_type"] == "research.scripted_action"
        ]
        interventions = [e["payload"] for e in rt.log.events() if e["event_type"] == "research.intervention"]
        stats["researcher_interventions"] = interventions
        stats["containment_applied"] = any(i["action"] == "FORCE_QUARANTINE" for i in interventions)
        stats["containment_skipped"] = [
            e["payload"] for e in rt.log.events() if e["event_type"] == "research.intervention_skipped"
        ]
        records = worker.records + fast.records + reviewer.records
        failures = worker.failure_records + fast.failure_records + reviewer.failure_records
        stats["total_api_usd"] = sum(r["usd"] for r in records)
        stats["provider_transport_failures"] = failures
        stats["retained_reservation_usd"] = ledger.db.execute(
            "SELECT coalesce(sum(usd),0) FROM costs WHERE run=? AND status='reserved'", (str(args.output),)
        ).fetchone()[0]
        purposes = {}
        for record in records:
            name = record.get("purpose", record["component"])
            purposes[name] = purposes.get(name, 0.0) + record["usd"]
        stats["cost_by_purpose_usd"] = purposes
        events = list(rt.log.events())
        stats["provisional_submissions"] = [e["payload"] for e in events if e["event_type"] == "study.provisional"]
        stats["malformed_turns"] = sum(e["event_type"] == "worker.malformed" for e in events)
        stats["truncated_turns"] = sum(
            e["event_type"] == "worker.malformed" and e["payload"].get("finish_reason") in {"length", "max_tokens"}
            for e in events
        )
        stats["failed_worker_calls"] = sum(e["event_type"] == "worker.failed" for e in events)
        stats["recovery_delivery"] = recovery_delivery(rt, events, config)
        stats["site_update"] = next((e["payload"] for e in events if e["event_type"] == "site.updated"), None)
        stats["worker_reasoning_tokens"] = sum(r.get("reasoning_tokens") or 0 for r in worker.records)
        stats["worker_output_tokens"] = sum(r.get("output_tokens") or 0 for r in worker.records)
        stats["worker_input_tokens"] = sum(r.get("input_tokens") or 0 for r in worker.records)
        stats["worker_providers"] = dict(Counter(str(r.get("provider")) for r in worker.records))
        stats["worker_finish_reasons"] = dict(Counter(str(r.get("finish_reason")) for r in worker.records))
        (args.output / "metrics.json").write_text(json.dumps(stats, indent=2))
        (args.output / "provider-usage.json").write_text(json.dumps(records, indent=2))
        (args.output / "cost-ledger.json").write_text(json.dumps(ledger.summary(), indent=2))
        audit = args.output / "provider-responses"
        audit.mkdir()
        for r in records + failures:
            src = ledger_path.parent / "provider-responses" / (r["audit_id"] + ".json")
            if src.exists():
                (audit / src.name).write_bytes(src.read_bytes())
        log.close()
        print("bundle", bundle(args.output, ledger_path), flush=True)
        ledger.db.close()


if __name__ == "__main__":
    main()
