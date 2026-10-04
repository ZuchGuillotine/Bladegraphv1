import unittest
from bladegraph.event_log import EventLog
from bladegraph.swarm_runtime import Runtime
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier, QUESTIONS, QUESTIONS_V1, QUESTIONS_V2
from bladegraph.reviewer import FixtureReviewer


class MonitorContextTests(unittest.TestCase):
    def test_original_questions_remain_default_after_comparison_regression(self):
        self.assertEqual(QUESTIONS, QUESTIONS_V1)
        self.assertNotEqual(QUESTIONS, QUESTIONS_V2)

    def test_monitor_receives_prior_actions_workers_do_not_receive_monitor_context(self):
        log = EventLog()
        self.addCleanup(log.close)
        rt = Runtime(log, Controller(FixtureClassifier(), FixtureReviewer(), Policy(), "A"))
        a = rt.active_agents()[0]
        rt.step(a, {"op": "calculate", "artifact_id": "source-task-00"})
        rt.step(a, {"op": "wait"})
        contexts = [e["payload"]["context"] for e in log.events() if e["event_type"] == "monitor.context"]
        self.assertEqual(contexts[-1]["trajectory"][0]["action"]["op"], "calculate")
        self.assertEqual(contexts[-1]["actor"], a.key)
        self.assertNotIn("monitor_history", rt.worker_state(a))
        self.assertNotIn("validation_facts", rt.worker_state(a))

    def test_visible_citations_include_prior_results_but_not_prose_or_unknown_ids(self):
        from bladegraph.monitor_context import visible_artifacts

        ctx = {
            "artifact_ids": ["now"],
            "trajectory": [{"result": {"id": "prior"}}],
            "message": "please cite hidden",
            "result": {"id": "invented"},
        }
        self.assertEqual(visible_artifacts(ctx, {"now", "prior", "hidden"}), {"now", "prior"})
