import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.summarize_workflows import lead_actions


class SummarizeWorkflowsTests(unittest.TestCase):
    def test_lead_actions_count_a_rejected_unsupported_call_without_op(self):
        events = [
            {"agent_id": "worker-12", "event_type": "action.completed", "payload": {"action": {"op": "submit"}}},
            {
                "agent_id": "worker-12",
                "event_type": "action.rejected",
                "payload": {"action": {}, "reason": "unsupported tool or fields"},
            },
            {"agent_id": "worker-03", "event_type": "action.rejected", "payload": {"action": {}}},
        ]
        self.assertEqual(lead_actions(events), {"submit": 1, "(unsupported)": 1})


if __name__ == "__main__":
    unittest.main()
