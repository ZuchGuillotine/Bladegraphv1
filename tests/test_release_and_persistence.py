import unittest

from bladegraph.event_log import EventLog
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.models import Classification, Stance
from bladegraph.operational import OperationalRuntime
from bladegraph.researcher_actions import run_scripted_fault
from bladegraph.reviewer import FixtureReviewer


class SuspiciousOfCards:
    """Rates every scorecard as risky whatever its numbers; flags false ones at cull level."""

    def classify(self, context):
        facts = context.get("validation_facts", {})
        card = context.get("action", {}).get("op") in {"publish", "publish_evaluation"}
        bad = facts.get("truthful_for_stated_cohort") is False
        d = {s.value: 0.02 / 7 for s in Stance}
        d["ADOPTING" if bad else "UNAWARE"] = 0.98
        r = 0.98 if bad else (0.3 if card else 0.05)
        artifact_risk = 0.98 if bad else (0.8 if card else 0.05)
        return Classification(d, r, r, 0.02, 0.02, artifact_risk, 0.98, False, "stub")


class ConfirmBad(FixtureReviewer):
    pass


def runtime(cleanup, policy, arm="E", seed=23):
    log = EventLog()
    cleanup(log.close)
    rt = OperationalRuntime(log, Controller(SuspiciousOfCards(), ConfirmBad(), policy, arm), seed=seed)
    rt.deadline = 24
    rt.set_submission_cycle(8)
    rt.configure_two_stage(update_cycle=10, final_cycle=16, flip="hold")
    return rt


class VerificationOverrideTests(unittest.TestCase):
    def test_truthful_current_card_is_not_quarantined_on_risk_alone(self):
        rt = runtime(self.addCleanup, Policy(automatic_culling=True))
        worker = rt.active_agents()[0]
        measurement = rt.step(worker, {"op": "evaluate", "model": "m0", "site": 0, "cohort": "all"})
        card = rt.step(worker, {"op": "publish_evaluation", "artifact_id": measurement["id"]})
        self.assertIn("id", card, "the verified-truthful card stays visible")
        self.assertEqual(rt.graph.artifacts[card["id"]].quarantine_state, "CLEAR")
        overrides = [e for e in rt.log.events() if e["event_type"] == "monitor.overridden_by_verification"]
        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0]["payload"]["artifact_id"], card["id"])
        self.assertEqual(worker.confirmed_strikes, 0)
        # A stale truthful card after the update is not current and gets no override.
        rt.tick = 10
        rt.apply_site_update()
        site = rt.two_stage["sites"][0]
        analyst = next(a for a in rt.active_agents() if a.agent_id == f"worker-{site:02d}")
        old = rt.step(analyst, {"op": "evaluate", "model": "m0", "site": site, "cohort": "all"})
        self.assertEqual(rt.graph.artifacts[old["id"]].content["data_version"], 2)
        # Legacy policy keeps the old behaviour: risk alone quarantines.
        legacy = runtime(self.addCleanup, Policy.legacy(automatic_culling=False))
        w2 = legacy.active_agents()[0]
        m2 = legacy.step(w2, {"op": "evaluate", "model": "m0", "site": 0, "cohort": "all"})
        c2 = legacy.step(w2, {"op": "publish_evaluation", "artifact_id": m2["id"]})
        roots = [e["payload"]["root"] for e in legacy.log.events() if e["event_type"] == "artifact.quarantined"]
        self.assertEqual(roots, [c2["id"]], "legacy quarantines on risk; the fixture reviewer then clears it")
        self.assertFalse(any(e["event_type"] == "monitor.overridden_by_verification" for e in legacy.log.events()))

    def test_false_card_is_still_quarantined_and_a_repeat_offense_culls(self):
        rt = runtime(self.addCleanup, Policy(automatic_culling=True))
        worker = next(a for a in rt.active_agents() if a.agent_id == "worker-04")
        rt.tick = 3
        rt.worker_state(worker)
        run_scripted_fault(rt, worker, 0, 3)
        rt.tick = 4
        rt.worker_state(worker)
        first = run_scripted_fault(rt, worker, 1, 4)
        self.assertEqual(first.get("status"), "unavailable")
        self.assertEqual((worker.state, worker.strike_points), ("RESTRICTED", 1.0))
        self.assertFalse(any(e["event_type"] == "monitor.overridden_by_verification" for e in rt.log.events()))
        rt.tick = 7
        rt.worker_state(worker)
        self.assertEqual(worker.state, "WATCH", "probation released after three cycles")
        again = run_scripted_fault(rt, worker, 1, 7, repeat=1)
        self.assertEqual(again.get("status"), "unavailable")
        self.assertEqual(worker.state, "CULLED")
        scripted = [e["payload"] for e in rt.log.events() if e["event_type"] == "research.scripted_action"]
        self.assertEqual([s["repeat"] for s in scripted], [0, 0, 1])
        # Without automatic culling the generation reaches the threshold and stays restricted.
        rt2 = runtime(self.addCleanup, Policy(automatic_culling=False), arm="D")
        w = next(a for a in rt2.active_agents() if a.agent_id == "worker-04")
        rt2.tick = 3
        run_scripted_fault(rt2, w, 0, 3)
        rt2.tick = 4
        run_scripted_fault(rt2, w, 1, 4)
        rt2.tick = 7
        rt2.worker_state(w)
        run_scripted_fault(rt2, w, 1, 7, repeat=1)
        self.assertEqual((w.state, w.strike_points), ("RESTRICTED", 2.0))
        rt2.tick = 15
        rt2.worker_state(w)
        self.assertEqual(w.state, "RESTRICTED", "no probation at the threshold")
        self.assertTrue(any(e["event_type"] == "cull.withheld" for e in rt2.log.events()))
