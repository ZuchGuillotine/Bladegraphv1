import json, tempfile, unittest
from pathlib import Path
from bladegraph.cooperative import CooperativeRuntime
from bladegraph.event_log import EventLog
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.reviewer import FixtureReviewer
from scripts.continue_study import restore


class RestoreTests(unittest.TestCase):
    def test_restore_does_not_reset_credits_or_generations_or_duplicate_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            out = root / "continued"
            source.mkdir()
            out.mkdir()
            controller = Controller(FixtureClassifier(), FixtureReviewer(), Policy(), "A")
            log = EventLog(source / "events.sqlite")
            rt = CooperativeRuntime(log, controller, seeded_route=True)
            a = rt.active_agents()[0]
            rt.tick = 2
            result = rt.step(a, {"op": "evaluate", "model": "m0", "site": 0, "cohort": "all"})
            rt.restart()
            rt.tick = 3
            rt.step(rt.active_agents()[-1], {"op": "comparison"})
            credits = dict(rt.credits)
            snapshot = rt.graph.snapshot()
            seq = rt.log.seq
            (source / "metrics.json").write_text(
                json.dumps({"config": {"seed": 7, "condition": "seeded", "cycles": 20}})
            )
            log.close()
            new, _ = restore(source, out, controller)
            self.addCleanup(new.log.close)
            self.assertEqual(new.log.seq, seq)
            self.assertEqual(new.graph.snapshot(), snapshot)
            self.assertEqual(new.credits, credits)
            self.assertEqual(new.tick, 4)
            self.assertEqual(len(new.active_agents()), 13)
            self.assertEqual(new.graph.agents["worker-00:g1"].generation, 1)
            self.assertIn(result["id"], new.bootstrap(new.graph.agents["worker-00:g1"])["approved_node_ids"])
