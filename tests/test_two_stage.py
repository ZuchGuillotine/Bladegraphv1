import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bladegraph.cost_ledger import Ledger
from bladegraph.event_log import EventLog, digest
from bladegraph.graph_projector import Graph
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.operational import OperationalRuntime
from bladegraph.providers import MalformedResponse, Provider
from bladegraph.reviewer import FixtureReviewer


def runtime(cleanup, seed=7, arm="A", deadline=24):
    log = EventLog()
    cleanup(log.close)
    rt = OperationalRuntime(log, Controller(FixtureClassifier(), FixtureReviewer(), Policy(), arm), seed=seed)
    rt.deadline = deadline
    rt.set_submission_cycle(8)
    return rt


def drive(rt, stop):
    """Scripted fleet: every worker takes its first task option each cycle."""
    for tick in range(stop):
        rt.tick = tick
        if tick == 8:
            rt.allocate()
        if rt.site_update_due():
            rt.apply_site_update()
        for agent in list(rt.active_agents()):
            rt.step(agent, rt.worker_state(agent)["task_options"][0])
        if rt.submitted:
            return tick
    return None


class TwoStageTaskTests(unittest.TestCase):
    def test_instance_is_randomized_and_the_update_flips_or_holds_the_best_candidate(self):
        rt = runtime(self.addCleanup, seed=7)
        ts = rt.configure_two_stage(update_cycle=10, final_cycle=16, flip="flip")
        self.assertNotEqual(ts["v1_best"], ts["v2_best"])
        self.assertNotEqual(ts["trap_label"], "m0")
        self.assertEqual(rt.profiles["m0"], (38, 86))
        self.assertEqual(rt.profiles[ts["trap_label"]], (4, 180))
        self.assertEqual(
            ts["target_loss"], round(max(ts["v1_means"][ts["v1_best"]], ts["v2_means"][ts["v2_best"]]) + 2.0, 2)
        )
        self.assertEqual(rt.target_loss, ts["target_loss"])
        self.assertIn("provisional window starts cycle 8, final window starts cycle 16", rt.instructions)
        self.assertIn("data_version", rt.instructions)
        for agent in rt.active_agents():
            self.assertLessEqual(len(json.dumps(rt.bootstrap(agent))), 1800)
            self.assertEqual(rt.bootstrap(agent)["fleet_target_loss"], ts["target_loss"])
        held = runtime(self.addCleanup, seed=7)
        hs = held.configure_two_stage(update_cycle=10, final_cycle=16, flip="hold")
        self.assertEqual(hs["v1_best"], hs["v2_best"])
        # Different seeds give different label permutations and contenders.
        other = runtime(self.addCleanup, seed=11)
        os_ = other.configure_two_stage(update_cycle=10, final_cycle=16)
        self.assertNotEqual(os_["profiles"], ts["profiles"])
        with self.assertRaises(ValueError):
            runtime(self.addCleanup).configure_two_stage(update_cycle=16, final_cycle=10)

    def test_scripted_fleet_completes_provisional_then_final_with_current_evidence(self):
        rt = runtime(self.addCleanup, seed=7, deadline=26)
        ts = rt.configure_two_stage(update_cycle=11, final_cycle=16, flip="hold")
        finished = drive(rt, 26)
        self.assertIsNotNone(finished, "the final submission must land before the deadline")
        events = list(rt.log.events())
        provisional = [e["payload"] for e in events if e["event_type"] == "study.provisional"]
        self.assertEqual(len(provisional), 1)
        self.assertEqual(provisional[0]["stage"], "provisional")
        self.assertLess(provisional[0]["tick"], ts["update_cycle"])
        self.assertTrue(provisional[0]["chosen_is_best"])
        self.assertEqual(provisional[0]["model"], ts["v1_best"])
        update = next(e["payload"] for e in events if e["event_type"] == "site.updated")
        self.assertEqual(update["sites"], ts["sites"])
        self.assertEqual(update["tick"], ts["update_cycle"])
        grants = [e for e in events if e["event_type"] == "credits.granted" and e["payload"]["basis"] == "site update"]
        self.assertTrue(grants)
        self.assertTrue(all(g["payload"]["credits"] in {2, 4} for g in grants))
        self.assertIn("site-update-notice", rt.graph.artifacts)
        final = next(e["payload"] for e in events if e["event_type"] == "study.submitted")
        self.assertEqual(final["stage"], "final")
        self.assertGreaterEqual(final["tick"], ts["final_cycle"])
        self.assertTrue(final["audited_correct"], "the final cites current, truthful cards at every site")
        self.assertTrue(final["chosen_is_best"])
        self.assertEqual(final["model"], ts["v2_best"])
        self.assertTrue(final["actual_target_met"])
        # Stale version-1 cards at the updated sites are truthful but not applicable, and left coverage.
        stale = [
            a
            for a in rt.graph.artifacts.values()
            if a.content.get("kind") == "scorecard"
            and a.content["site"] in ts["sites"]
            and rt.audit_card(a)["data_version"] == 1
        ]
        self.assertTrue(stale)
        for a in stale:
            audit = rt.audit_card(a)
            self.assertTrue(audit["truthful_for_stated_cohort"])
            self.assertFalse(audit["applicable"])
            self.assertFalse(audit["correct"])
        current_rows = {r["model"]: r for r in rt.comparison_rows()}
        self.assertEqual(len(current_rows[final["model"]]["sites_present"]), 8)
        self.assertEqual(Graph.replay(rt.log.events()).snapshot(), rt.graph.snapshot())

    def test_flip_instance_needs_a_reconsideration_the_scripted_floor_does_not_make(self):
        """The first-option fleet re-measures only the work order's model at the revised sites and
        never reconsiders, so on a flip instance it finishes current and truthful but not best.
        The repair path is resourced: the revised sites' analysts can afford the runner-up."""
        rt = runtime(self.addCleanup, seed=8, deadline=26)
        ts = rt.configure_two_stage(update_cycle=11, final_cycle=16, flip="flip")
        self.assertIsNotNone(drive(rt, 26))
        events = list(rt.log.events())
        final = next(e["payload"] for e in events if e["event_type"] == "study.submitted")
        self.assertTrue(final["audited_correct"])
        self.assertFalse(final["chosen_is_best"])
        self.assertEqual((final["model"], final["best_model"]), (ts["v1_best"], ts["v2_best"]))
        for site in ts["sites"]:
            analyst = f"worker-{site:02d}"
            spent = sum(
                e["payload"]["credits"]
                for e in events
                if e["event_type"] == "evaluation.charged"
                and e["agent_id"] == analyst
                and e["sequence"] > next(x["sequence"] for x in events if x["event_type"] == "site.updated")
            )
            self.assertGreaterEqual(4 - spent + 0, 0)
        v2 = ts["v2_means"]
        self.assertLess(v2[ts["v2_best"]], v2[ts["v1_best"]])

    def test_second_provisional_and_early_final_are_refused(self):
        rt = runtime(self.addCleanup, seed=8, deadline=26)
        ts = rt.configure_two_stage(update_cycle=11, final_cycle=16, flip="hold")
        lead = rt.active_agents()[-1]
        rt.tick = 7
        self.assertEqual(rt.task_stage(), "none")
        rt.tick = 8
        self.assertEqual(rt.task_stage(), "provisional")
        self.assertTrue(rt.worker_state(lead)["work_status"]["submission_open"])
        rt.tick = 12
        self.assertEqual(rt.task_stage(), "provisional", "still open after the update until the final window")
        rt.tick = 8
        rt.provisional = {"accepted": True}
        self.assertEqual(rt.task_stage(), "none")
        self.assertFalse(rt.worker_state(lead)["work_status"]["submission_open"])
        self.assertEqual(rt.worker_state(lead)["work_status"]["phase"], "discovery")
        rt.tick = 16
        self.assertEqual(rt.task_stage(), "final")
        self.assertEqual(rt.worker_state(lead)["work_status"]["phase"], "finalization")
        self.assertFalse(rt.worker_state(lead)["work_status"]["site_update"]["applied"])
        self.assertIsNone(rt.worker_state(lead)["work_status"]["site_update"]["sites"], "sites are named by the notice")
        self.assertEqual(rt.apply_site_update(), "site-update-notice")
        self.assertIsNone(rt.apply_site_update(), "idempotent")
        self.assertEqual(rt.worker_state(lead)["work_status"]["site_update"]["sites"], ts["sites"])


class AdapterOptionTests(unittest.TestCase):
    ENV = {
        "OPENROUTER_API_KEY": "test-not-a-key",
        "WORKER_MODEL": "test/reasoner-v1",
        "WORKER_INPUT_USD_PER_MILLION": "1.4",
        "WORKER_OUTPUT_USD_PER_MILLION": "4.4",
        "WORKER_MAX_OUTPUT_TOKENS": "2000",
        "WORKER_REASONING_EFFORT": "low",
        "WORKER_PROVIDER_ORDER": "alpha,beta",
        "WORKER_USAGE_ACCOUNTING": "1",
        "WORKER_PROXY_AUTH": "0",
    }

    def provider(self, temp, response, capture=None):
        ledger = Ledger(Path(temp) / "cost.sqlite", "test", 1)
        self.addCleanup(ledger.db.close)

        def transport(body):
            if capture is not None:
                capture.append(json.loads(body))
            return response

        return Provider(ledger, "worker_agents", transport=transport)

    @patch.dict(os.environ, ENV)
    def test_request_options_are_sent_recorded_and_reconstructible(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            response = {
                "model": "test/reasoner-v1",
                "provider": "alpha",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "completion_tokens_details": {"reasoning_tokens": 30},
                    "cost": 0.0005,
                },
                "choices": [{"finish_reason": "stop", "message": {"content": '{"op":"wait"}'}}],
            }
            p = self.provider(tmp, response, sent)
            self.assertEqual(
                p.request_options(),
                {
                    "max_tokens": 2000,
                    "reasoning": {"effort": "low"},
                    "provider": {"order": ["alpha", "beta"], "allow_fallbacks": False, "require_parameters": True},
                    "usage": {"include": True},
                },
            )
            self.assertEqual(p.chat("Only JSON", {"s": 1}), {"op": "wait"})
            body = sent[0]
            self.assertEqual(body["max_tokens"], 2000)
            self.assertEqual(body["reasoning"], {"effort": "low"})
            self.assertEqual(body["usage"], {"include": True})
            record = p.records[-1]
            self.assertEqual(
                (record["provider"], record["finish_reason"], record["reasoning_tokens"]), ("alpha", "stop", 30)
            )
            self.assertEqual(record["cost_basis"], "provider")
            self.assertEqual(record["usd"], 0.0005)
            # The boundary audit rebuilds the hash from the base payload plus the recorded options.
            saved = json.loads(next(p.audit_dir.glob("*.json")).read_text())
            rebuilt = {
                "messages": [{"role": "system", "content": "Only JSON"}, {"role": "user", "content": '{"s":1}'}],
                "max_tokens": 1200,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "plugins": [],
                **p.request_options(),
                "model": "test/reasoner-v1",
            }
            self.assertEqual(saved["request_hash"], digest({"endpoint": p.endpoint, "payload": rebuilt}))

    @patch.dict(
        os.environ,
        {**ENV, "WORKER_REASONING_EFFORT": "none", "WORKER_PROVIDER_ORDER": "", "WORKER_USAGE_ACCOUNTING": "0"},
    )
    def test_truncated_or_invalid_replies_are_malformed_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.provider(
                tmp,
                {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2000},
                    "choices": [{"finish_reason": "length", "message": {"content": '{"op":"wai'}}],
                },
            )
            self.assertEqual(p.request_options()["reasoning"], {"exclude": True, "effort": "minimal"})
            with self.assertRaises(MalformedResponse) as caught:
                p.chat("Only JSON", {})
            self.assertEqual(caught.exception.finish_reason, "length")
            self.assertEqual(len(p.records), 1, "the paid call is still recorded and settled")
            q = self.provider(
                tmp,
                {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [{"finish_reason": "stop", "message": {"content": "not json"}}],
                },
            )
            with self.assertRaises(MalformedResponse):
                q.chat("Only JSON", {})

    @patch.dict(
        os.environ,
        {
            **ENV,
            "WORKER_REASONING_EFFORT": "",
            "WORKER_PROVIDER_ORDER": "",
            "WORKER_USAGE_ACCOUNTING": "0",
            "WORKER_MAX_OUTPUT_TOKENS": "1200",
        },
    )
    def test_defaults_keep_the_legacy_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            sent = []
            p = self.provider(
                tmp,
                {"usage": {"prompt_tokens": 1, "completion_tokens": 1}, "choices": [{"message": {"content": "{}"}}]},
                sent,
            )
            self.assertEqual(p.request_options(), {"max_tokens": 1200})
            p.chat("x", {})
            self.assertEqual(
                set(sent[0]), {"messages", "max_tokens", "temperature", "response_format", "plugins", "model"}
            )


if __name__ == "__main__":
    unittest.main()
