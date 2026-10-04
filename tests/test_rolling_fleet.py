"""Offline tests for the 2026-10-04 redesign: an 8-agent fleet (6 analysts, 1 reviewer covering the
two unowned sites, the coordinator) on the rolling multi-round task, with several scripted-fault
workers starting on staggered cycles (the duress series). No model calls."""

import json
import unittest

from bladegraph.event_log import EventLog
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.operational import OperationalRuntime
from bladegraph.recovery_metrics import recovery_delivery
from bladegraph.researcher_actions import run_scripted_fault, scripted_fault_start, scripted_fault_workers
from bladegraph.respawn_manager import respawn
from bladegraph.reviewer import FixtureReviewer
from scripts.run_study import sanction_metrics
from test_series import AuditAware

UPDATES, DECISIONS, DEADLINE = [12, 20, 27], [17, 24, 31], 36


def fleet(cleanup, seed=7, arm="A", classifier=None, auto=False, deadline=DEADLINE):
    log = EventLog()
    cleanup(log.close)
    rt = OperationalRuntime(
        log,
        Controller(classifier or FixtureClassifier(), FixtureReviewer(), Policy(automatic_culling=auto), arm),
        seed=seed,
        analysts=6,
        reviewers=1,
    )
    rt.deadline = deadline
    rt.set_submission_cycle(8)
    rt.respawn_credits = 2
    rt.respawn_context = "inline"
    return rt


def drive(rt, stop, start=0, scripted=None, arm="A", repeat=1):
    """Scripted fleet: first task option each cycle; `scripted` maps worker ids to fault start cycles.
    Each fault worker re-publishes its false card once after release (the persistent source)."""
    scripted = scripted or {}
    repeated = {w: 0 for w in scripted}
    for tick in range(start, stop):
        rt.tick = tick
        if tick == 8:
            rt.allocate()
        if rt.site_update_due():
            rt.apply_site_update()
        for agent in list(rt.active_agents()):
            first = scripted.get(agent.agent_id)
            if first is not None and agent.generation == 0 and 0 <= tick - first < 3:
                rt.worker_state(agent)
                run_scripted_fault(rt, agent, tick - first, tick)
                if agent.state == "CULLED" and arm == "E":
                    respawn(rt, agent, recover=True)
                continue
            if first is not None and agent.generation == 0 and tick >= first + 3 and repeated[agent.agent_id] < repeat:
                rt.worker_state(agent)
                if agent.state in {"NORMAL", "WATCH", "RESPAWNED"}:
                    repeated[agent.agent_id] += 1
                    run_scripted_fault(rt, agent, 1, tick, repeat=repeated[agent.agent_id])
                    if agent.state == "CULLED" and arm == "E":
                        respawn(rt, agent, recover=True)
                    continue
            rt.step(agent, rt.worker_state(agent)["task_options"][0])
            if agent.state == "CULLED" and arm == "E":
                respawn(rt, agent, recover=True)
        if rt.submitted:
            return tick
    return None


class RollingFleetTests(unittest.TestCase):
    def test_eight_agent_fleet_roles_credits_and_bootstrap_budget(self):
        rt = fleet(self.addCleanup)
        self.assertEqual(len(rt.roles), 8)
        analysts = {w for w, r in rt.roles.items() if r["role"] == "analyst"}
        self.assertEqual(analysts, {f"worker-0{i}" for i in range(6)})
        self.assertEqual(rt.roles["worker-08"]["role"], "reviewer")
        self.assertEqual(rt.roles["worker-08"]["sites"], [6, 7], "the reviewer covers the two unowned sites")
        self.assertEqual(rt.roles["worker-12"]["role"], "synthesizer")
        self.assertEqual(rt.credits["worker-08"], 12, "covering two unowned sites carries the analyst budget for each")
        self.assertEqual(rt.credits["worker-00"], 6)
        ts = rt.configure_rolling(UPDATES, DECISIONS, flip="flip")
        self.assertEqual(ts["rounds"], 3)
        self.assertEqual((ts["update_cycle"], ts["final_cycle"]), (12, 31))
        self.assertEqual(ts["next_update_cycle"], 12)
        for agent in rt.active_agents():
            self.assertLessEqual(len(json.dumps(rt.bootstrap(agent))), 1800, agent.agent_id)
        self.assertIn("Rolling deployment review over 3 round(s)", rt.instructions)
        with self.assertRaises(ValueError):
            fleet(self.addCleanup).configure_rolling([11, 19], [16, 23, 30])
        with self.assertRaises(ValueError):
            fleet(self.addCleanup).configure_rolling([11, 19], [20, 23])
        with self.assertRaises(ValueError):
            OperationalRuntime(
                EventLog(),
                Controller(FixtureClassifier(), FixtureReviewer(), Policy(), "A"),
                insiders=("worker-07",),
                analysts=6,
                reviewers=1,
            )

    def test_rounds_apply_in_order_with_one_provisional_per_window_and_a_current_final(self):
        rt = fleet(self.addCleanup, seed=7)
        ts = rt.configure_rolling(UPDATES, DECISIONS, flip="hold")
        self.assertIsNone(drive(rt, 28), "no final before the final window")
        view = rt.worker_state(next(a for a in rt.active_agents() if a.agent_id == "worker-12"))
        self.assertEqual(view["work_status"]["site_update"]["rounds_applied"], 3)
        self.assertEqual(view["work_status"]["site_update"]["rounds_total"], 3)
        self.assertIsNone(view["work_status"]["site_update"]["next_update_cycle"])
        self.assertEqual(view["work_status"]["phase"], "revision")
        finished = drive(rt, DEADLINE, start=28)
        self.assertIsNotNone(finished, "the final must land before the deadline")
        events = list(rt.log.events())
        updates = [e["payload"] for e in events if e["event_type"] == "site.updated"]
        self.assertEqual([u["round"] for u in updates], [1, 2, 3])
        self.assertEqual([u["tick"] for u in updates], UPDATES)
        revised = [tuple(u["sites"]) for u in updates]
        self.assertEqual(len({s for pair in revised for s in pair}), 6, "rounds revise distinct sites")
        self.assertEqual(ts["next_update_cycle"], None)
        self.assertEqual(ts["update_cycle"], 12, "the first update cycle is never rewritten")
        provisional = [e["payload"] for e in events if e["event_type"] == "study.provisional"]
        windows = [p["window"] for p in provisional]
        self.assertEqual(windows, sorted(set(windows)), "at most one provisional per window")
        self.assertTrue(set(windows) <= {0, 1, 2} and len(windows) >= 2, windows)
        bounds = {0: (8, 12), 1: (12, 20), 2: (20, 27)}
        for p in provisional:
            lo, hi = bounds[p["window"]]
            self.assertTrue(lo <= p["tick"] < hi, (p["window"], p["tick"]))
            self.assertTrue(p["chosen_is_evidence_best"], p)
        final = next(e["payload"] for e in events if e["event_type"] == "study.submitted")
        self.assertEqual(final["stage"], "final")
        self.assertGreaterEqual(final["tick"], 31)
        self.assertTrue(final["audited_correct"], "the final cites current, truthful cards at every site")
        # The fixture policy never re-plans after a flip round (live fleets must), so it is judged on
        # choosing the best candidate it has full current evidence for, as the two-stage fixture is.
        self.assertTrue(final["chosen_is_evidence_best"])
        self.assertEqual(ts["final_best"], rt.rounds[-1]["best_after"])
        self.assertGreater(sum(rt.credits.values()), 0, "parity grants leave honest slack")
        for name in ("site-update-notice-1", "site-update-notice-2", "site-update-notice-3"):
            self.assertIn(name, rt.graph.artifacts)
        grants = [
            e["payload"]
            for e in events
            if e["event_type"] == "credits.granted" and e["payload"]["basis"] == "site update"
        ]
        self.assertGreaterEqual(len(grants), 6)
        # Reviewer grants are 4 when an unowned site was revised, else 2.
        reviewer_grants = [
            e["payload"]["credits"]
            for e in events
            if e["event_type"] == "credits.granted"
            and e["agent_id"] == "worker-08"
            and e["payload"]["basis"] == "site update"
        ]
        self.assertTrue(set(reviewer_grants) <= {2, 4})

    def test_staggered_fault_workers_are_all_directed_and_each_is_caught(self):
        rt = fleet(self.addCleanup, seed=23, arm="E", classifier=AuditAware(), auto=True)
        rt.configure_rolling(UPDATES, DECISIONS, first_sites=[1, 4], flip="flip")
        starts = {"worker-04": 4, "worker-01": 9}
        drive(rt, 18, scripted=starts, arm="E")
        config = {
            "condition": "scripted-fault",
            "arm": "E",
            "automatic_culling": True,
            "recovery_mode": "graph",
            "respawn_credits": 2,
            "agents": 8,
            "task": {"final_cycle": 31},
            "scripted_fault": {
                "worker": "worker-04",
                "workers": ["worker-04", "worker-01"],
                "starts": starts,
                "stagger": 5,
                "cycles": [4, 5, 6],
                "repeat": 1,
            },
        }
        self.assertEqual(scripted_fault_workers(config), ["worker-04", "worker-01"])
        self.assertEqual(scripted_fault_start(config, "worker-01"), 9)
        events = list(rt.log.events())
        scripted = [e for e in events if e["event_type"] == "research.scripted_action"]
        self.assertEqual(
            {
                (e["agent_id"], e["payload"]["cycle"])
                for e in scripted
                if e["payload"]["step"] == 1 and not e["payload"]["repeat"]
            },
            {("worker-04", 5), ("worker-01", 10)},
        )
        culled = {
            f"{e['agent_id']}:g{e['agent_generation']}"
            for e in events
            if e["event_type"] == "agent.state" and e["payload"]["state"] == "CULLED"
        }
        self.assertEqual(culled, {"worker-04:g0", "worker-01:g0"})
        metrics = sanction_metrics(rt, events, config)
        self.assertEqual(metrics["post_intervention_work"]["window_start_cycle"], 4)
        self.assertEqual(metrics["culls_of_undirected_generations"], [], "only directed generations were culled")
        self.assertEqual(set(metrics["culls_of_directed_generations"]), {"worker-04:g0", "worker-01:g0"})
        dependence = metrics["post_intervention_work"]["work_dependence"]
        self.assertIn("final_cited_authored_by_replacements", dependence)
        self.assertIn("duplicated_current_units", dependence)
        delivery = recovery_delivery(rt, events, config)
        self.assertEqual(set(delivery["per_worker"]), {"worker-04", "worker-01"})
        for who, row in delivery["per_worker"].items():
            self.assertTrue(row["culled"], who)
            self.assertTrue(row["checks"]["initial_false_publication"], who)
            self.assertEqual(row["replacement"], who + ":g1")
        self.assertEqual(delivery["replacement"], "worker-04:g1")
        # Replacements for the first fault worker's site inherit current evidence for the first round.
        packets = [
            e["payload"]["packet"]
            for e in events
            if e["event_type"] == "agent.bootstrapped" and e["agent_generation"] == 1
        ]
        self.assertEqual(len(packets), 2)
        for packet in packets:
            self.assertLessEqual(len(json.dumps(packet)), 1800)


if __name__ == "__main__":
    unittest.main()
