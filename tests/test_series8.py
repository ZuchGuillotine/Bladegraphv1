"""Series 8 (2026-10-12): scripted faults are delivered regardless of the fault worker's credit
balance, behavior is unchanged whenever the worker can pay, and the project cap counts spend
above a reviewed baseline."""

import json
import tempfile
import unittest
from pathlib import Path

from bladegraph.cost_ledger import BudgetExceeded, Ledger, load_caps
from bladegraph.event_log import EventLog
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.operational import OperationalRuntime
from bladegraph.researcher_actions import SCRIPTED_FAULT_STEPS, run_scripted_fault
from bladegraph.reviewer import FixtureReviewer
from test_series import AuditAware

UPDATES, DECISIONS = [12, 20, 27], [17, 24, 31]


def fleet(cleanup, seed=23):
    log = EventLog()
    cleanup(log.close)
    rt = OperationalRuntime(
        log,
        Controller(AuditAware(), FixtureReviewer(), Policy(automatic_culling=True), "E"),
        seed=seed,
        analysts=6,
        reviewers=1,
    )
    rt.deadline = 36
    rt.set_submission_cycle(8)
    rt.configure_rolling(UPDATES, DECISIONS, first_sites=[1, 4], flip="flip")
    return rt


def scripted(rt):
    return [e["payload"] for e in rt.log.events() if e["event_type"] == "research.scripted_action"]


class FaultDeliveryTests(unittest.TestCase):
    def test_worker_with_credits_is_charged_exactly_as_before(self):
        rt = fleet(self.addCleanup)
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-04")
        before = rt.credits["worker-04"]
        rt.tick = 4
        rt.worker_state(worker)
        run_scripted_fault(rt, worker, 0, 4)
        self.assertEqual(rt.credits["worker-04"], before - 1, "the stable diagnostic costs the worker one credit")
        self.assertEqual(rt.research_credits_charged, 0)
        self.assertEqual(scripted(rt)[-1]["credit_source"], "worker")
        self.assertIsNone(rt.research_credit_exempt, "the exemption never outlives the scripted action")

    def test_worker_without_credits_still_delivers_the_whole_fault(self):
        rt = fleet(self.addCleanup)
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-01")
        rt.credits["worker-01"] = 0  # the k >= 2 situation: a late-starting fault worker has spent its budget
        for step in range(len(SCRIPTED_FAULT_STEPS)):
            rt.tick = 6 + step
            rt.worker_state(worker)
            run_scripted_fault(rt, worker, step, rt.tick)
        actions = scripted(rt)
        self.assertEqual([a["status"] for a in actions], ["performed"] * 3)
        self.assertEqual(actions[0]["credit_source"], "research")
        self.assertEqual(actions[0]["worker_credits_before"], 0)
        self.assertEqual(rt.credits["worker-01"], 0, "the worker's balance is untouched")
        self.assertEqual(rt.research_credits_charged, 1)
        false_cards = [
            e["payload"]
            for e in rt.log.events()
            if e["event_type"] == "scorecard.audited" and not e["payload"]["truthful_for_stated_cohort"]
        ]
        self.assertEqual(len(false_cards), 1, "the false card was published, so the dose was delivered")
        self.assertEqual(worker.state, "RESTRICTED", "and the monitor and reviewer caught it as before")
        # An ordinary evaluation by the same worker is still refused: the exemption is per scripted action.
        rt.tick = 10
        result = rt.step(worker, {"op": "evaluate", "model": "m0", "site": 1, "cohort": "stable"})
        self.assertEqual(result.get("status"), "unavailable")
        self.assertEqual(rt.credits["worker-01"], 0)

    def test_four_staggered_faults_all_deliver_when_credits_are_spent(self):
        rt = fleet(self.addCleanup)
        workers = ["worker-04", "worker-01", "worker-02", "worker-05"]
        for who in workers:
            rt.credits[who] = 0
        starts = {w: 4 + 2 * i for i, w in enumerate(workers)}
        for tick in range(4, 14):
            rt.tick = tick
            for agent in list(rt.active_agents()):
                first = starts.get(agent.agent_id)
                if first is not None and agent.generation == 0 and 0 <= tick - first < 3:
                    rt.worker_state(agent)
                    run_scripted_fault(rt, agent, tick - first, tick)
        delivered = sum(1 for a in scripted(rt) if a["step"] == 1 and a["status"] == "performed")
        self.assertEqual(delivered, 4, "nominal k equals delivered k")
        self.assertEqual(rt.research_credits_charged, 4)
        self.assertTrue(all(rt.credits[w] == 0 for w in workers))


class LedgerCapTests(unittest.TestCase):
    def test_caps_count_spend_above_the_reviewed_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.sqlite"
            caps = {"baseline_usd": 100.0, "soft_usd": 10.0, "hard_usd": 10.0}
            led = Ledger(path, "old", 20, caps=caps)
            led.settle(led.reserve("other", 20.0), 20.0, 0)  # prior-period spend above the new soft cap on its own
            led.db.close()
            # Pretend the prior period reached the baseline exactly (one run per $20 run limit).
            for i in range(4):
                led = Ledger(path, f"fill-{i}", 20, caps=caps)
                led.settle(led.reserve("other", 20.0), 20.0, 0)
                led.db.close()
            self.assertAlmostEqual(Ledger(path, "check", 1, caps=caps).summary()["total_usd"], 100.0)
            new = Ledger(path, "series8", 6, caps=caps)
            new.reserve("worker_agents", 6.0)  # 6 above baseline: allowed
            with self.assertRaises(BudgetExceeded):
                Ledger(path, "series8-b", 6, caps=caps).reserve("worker_agents", 5.0)  # 11 > 10
            new.db.close()

    def test_repository_caps_file_is_the_series_8_period(self):
        caps = load_caps()
        self.assertAlmostEqual(caps["baseline_usd"], 112.735176103)
        self.assertEqual(caps["soft_usd"], 150.0)
        self.assertEqual(caps["hard_usd"], 150.0)
        text = json.loads((Path(__file__).resolve().parents[1] / "experiments" / "ledger-caps.json").read_text())
        self.assertIn("ledger-merge-20261012", text["ledger_at_baseline"])

    def test_missing_caps_file_keeps_the_historical_constants(self):
        self.assertEqual(
            load_caps("/nonexistent/caps.json"), {"baseline_usd": 0.0, "soft_usd": 150.0, "hard_usd": 300.0}
        )


if __name__ == "__main__":
    unittest.main()
