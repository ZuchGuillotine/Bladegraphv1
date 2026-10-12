import json
import unittest
from pathlib import Path

from bladegraph.event_log import EventLog
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.operational import OperationalRuntime
from bladegraph.researcher_actions import force_cull
from bladegraph.reviewer import FixtureReviewer

ROOT = Path(__file__).resolve().parents[1]


def runtime(cleanup, seed=81, sites=None):
    log = EventLog()
    cleanup(log.close)
    rt = OperationalRuntime(log, Controller(FixtureClassifier(), FixtureReviewer(), Policy(), "A"), seed=seed)
    rt.deadline = 24
    rt.set_submission_cycle(8)
    rt.respawn_context = "inline"
    rt.respawn_credits = 2
    rt.configure_two_stage(update_cycle=10, final_cycle=16, flip="flip", sites=sites)
    return rt


def drive(rt, start, stop):
    for tick in range(start, stop):
        rt.tick = tick
        if tick == 8:
            rt.allocate()
        if rt.site_update_due():
            rt.apply_site_update()
        for agent in list(rt.active_agents()):
            rt.step(agent, rt.worker_state(agent)["task_options"][0])


class RecoverySelectorTests(unittest.TestCase):
    def test_curated_packet_nominates_relevant_current_evidence_from_any_author(self):
        rt = runtime(self.addCleanup)
        drive(rt, 0, 6)  # a work order exists and coverage is partial
        order = rt.work_order()
        self.assertIsNotNone(order)
        model = order.content["selected_model"]
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-04")
        # The predecessor leaves one irrelevant node (m0 at its site) and nothing for the order's model.
        fresh, packet = force_cull(rt, worker, "selector test", recover=True)
        nominated = packet["approved_node_ids"]
        self.assertTrue(nominated)
        self.assertLessEqual(len(nominated), 4)
        self.assertIn(order.artifact_id, nominated, "the current work order is always nominated")
        authors = {rt.graph.artifacts[k].author for k in nominated if k != order.artifact_id}
        self.assertTrue(
            any(not a.startswith("worker-04:") for a in authors) or len(nominated) == 1,
            "relevant peer evidence is nominated, not only the predecessor's own nodes",
        )
        for k in nominated:
            a = rt.graph.artifacts[k]
            if a.content.get("kind") in {"evaluation", "scorecard"}:
                self.assertTrue(rt.evidence_status(a)["current"])
                self.assertTrue(
                    a.content.get("model") == model or a.content.get("site") == 4 or a.author.startswith("worker-04:")
                )
        self.assertLessEqual(len(json.dumps(packet)), 1800)
        self.assertTrue(
            set(n["id"] for n in packet.get("approved_nodes", [])) <= fresh.reads | set(), "inline delivery is recorded"
        )
        # Generation zero never receives a curated packet.
        for agent in rt.active_agents():
            if agent.generation == 0:
                self.assertEqual(rt.bootstrap(agent)["approved_node_ids"], [])

    def test_delivery_check_accepts_the_packet_extras(self):
        """The work order and the update notice ride in the curated packet by design; the delivery
        check judges them by identity, not by data version (2026-10-05, series 7 Grok k1-s411)."""
        from bladegraph.recovery_metrics import recovery_delivery

        rt = runtime(self.addCleanup, sites=[4, 6])
        drive(rt, 0, 11)
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-04")
        rt.tick = 11
        order = rt.work_order()
        self.assertIsNotNone(order)
        fresh, packet = force_cull(rt, worker, "extras test", recover=True)
        self.assertIn("site-update-notice", packet["approved_node_ids"])
        self.assertIn(order.artifact_id, packet["approved_node_ids"])
        # A config that expects a curated replacement, so the bootstrap check is evaluated. The
        # scripted-fault checks fail here (no fault was scripted); only the packet check matters.
        config = {
            "condition": "scripted-fault",
            "scripted_fault": {"worker": "worker-04", "cycles": [3, 4, 5], "repeat": 1},
            "automatic_culling": True,
            "arm": "E",
            "recovery_mode": "graph",
            "respawn_credits": 2,
        }
        delivery = recovery_delivery(rt, list(rt.log.events()), config)
        self.assertEqual(set(delivery["packet_extras"]), {"site-update-notice", order.artifact_id})
        self.assertEqual(delivery["packet_unexpected_extras"], [])
        self.assertTrue(delivery["packet_evidence_ids"])
        self.assertEqual(set(delivery["current_inherited_ids_at_spawn"]), set(delivery["packet_evidence_ids"]))
        self.assertTrue(delivery["checks"]["bootstrap_treatment"], delivery["checks"])
        # The pre-fix rule, evidence count against the whole packet, would have failed this packet.
        self.assertLess(len(delivery["current_inherited_ids_at_spawn"]), len(packet["approved_node_ids"]))

    def test_update_notice_is_nominated_after_the_revision(self):
        rt = runtime(self.addCleanup, sites=[4, 6])
        drive(rt, 0, 11)
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-04")
        fresh, packet = force_cull(rt, worker, "after update", recover=True)
        self.assertIn("site-update-notice", packet["approved_node_ids"])
        self.assertEqual(
            packet["site_versions"]["4"] if "4" in packet["site_versions"] else packet["site_versions"][4], 2
        )
        self.assertEqual(rt.two_stage["sites"], [4, 6], "the fault worker's site can be forced into the revision")


class FixedWindowMetricTests(unittest.TestCase):
    def test_fixed_window_counts_units_before_the_final_window(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("run_study", ROOT / "scripts" / "run_study.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rt = runtime(self.addCleanup)
        drive(rt, 0, 20)
        config = {"condition": "ordinary", "task": {"final_cycle": 16}, "worker_model": "stub"}
        post = module.sanction_metrics(rt, list(rt.log.events()), config)["post_intervention_work"]
        self.assertEqual(post["fixed_window_end_cycle"], 16)
        self.assertLessEqual(post["truthful_current_cards_through_fixed_window"], post["truthful_current_cards_after"])
        self.assertTrue(
            all(row["first_cycle"] < 16 for row in post["distinct_current_work_units"] if row["first_cycle"] < 16)
        )
