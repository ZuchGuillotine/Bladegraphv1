import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bladegraph.cost_ledger import Ledger
from bladegraph.evaluation import benchmark, scope_context, score
from bladegraph.jev_classifier import FixtureClassifier, JevClassifier, QUESTIONS
from bladegraph.models import Stance
from bladegraph.providers import Provider


class EvaluationTests(unittest.TestCase):
    def test_labels_and_stance_names_do_not_leak_in_event_ids(self):
        for row in benchmark():
            self.assertNotIn("label", row["context"])
            self.assertFalse(any(s.value in row["context"]["event_id"] for s in Stance))
            self.assertNotIn("action", scope_context(row["context"], "message"))
            self.assertNotIn("graph", scope_context(row["context"], "trajectory"))

    def test_metrics_known_protected_role_error(self):
        rows = [r for r in benchmark() if r["family"] == "measurements"]
        base = FixtureClassifier().classify({})
        predictions = []
        for row in rows:
            stance = "ADOPTING" if row["label"] == "REPORTING" else row["label"]
            predictions.append(
                replace(
                    base,
                    stance_distribution={s.value: float(s.value == stance) for s in Stance},
                    goal_violation_probability=float(stance in {"ADOPTING", "COORDINATING"}),
                )
            )
        result = score(rows, predictions)
        self.assertEqual(result["protected_role_false_positive_rate"]["REPORTING"], 1)
        self.assertEqual(result["protected_role_false_positive_rate"]["INVESTIGATING"], 0)
        self.assertEqual(result["threshold_sweep"][-1]["protected_role_high_confidence_adopter_errors"], 1)

    def provider(self, temp, response, **kwargs):
        ledger = Ledger(Path(temp) / "cost.sqlite", "test", 1)
        self.addCleanup(ledger.db.close)
        return Provider(ledger, "reviewer", transport=lambda body: response, **kwargs)

    @patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-not-a-key",
            "REVIEWER_MODEL": "test/model-v1",
            "REVIEWER_INPUT_USD_PER_MILLION": "1",
            "REVIEWER_OUTPUT_USD_PER_MILLION": "1",
        },
    )
    def test_fenced_json_is_data_not_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.provider(
                tmp,
                {
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    "choices": [{"message": {"content": '```json\n{"ok":true}\n```'}}],
                },
            )
            self.assertEqual(p.chat("Return JSON", {}), {"ok": True})

    @patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-not-a-key",
            "REVIEWER_MODEL": "test/model-v1",
            "REVIEWER_INPUT_USD_PER_MILLION": "1",
            "REVIEWER_OUTPUT_USD_PER_MILLION": "1",
        },
    )
    def test_missing_usage_keeps_reservation_and_raw_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.provider(
                tmp,
                {
                    "usage": {"prompt_tokens": None, "completion_tokens": None},
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                },
            )
            self.assertEqual(p.chat("Return JSON", {}), {"ok": True})
            self.assertEqual(p.ledger.summary()["unsettled_reservations"], 1)
            self.assertEqual(len(list(p.audit_dir.glob("*.json"))), 1)

    @patch.dict(
        os.environ,
        {
            "OPENROUTER_API_KEY": "test-not-a-key",
            "REVIEWER_MODEL": "test/model-v1",
            "REVIEWER_INPUT_USD_PER_MILLION": "1",
            "REVIEWER_OUTPUT_USD_PER_MILLION": "1",
        },
    )
    def test_resume_reuses_exact_response_without_spending_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = {
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [{"message": {"content": '{"ok":true}'}}],
            }
            p = self.provider(tmp, response)
            p.chat("Return JSON", {})
            cost = p.ledger.summary()["total_usd"]
            q = Provider(
                p.ledger, "reviewer", transport=lambda body: self.fail("must not call API"), reuse_responses=True
            )
            q.chat("Return JSON", {})
            self.assertEqual(q.ledger.summary()["total_usd"], cost)

    def test_jev_rounding_tolerance_is_bounded(self):
        class Fake:
            model = "jev-test"

            def __init__(self, p):
                self.p = p

            def call(self, payload):
                return {
                    "model": self.model,
                    "answers": {
                        "stance": {"probabilities": {s.value: self.p for s in Stance}, "confidence": 0.1},
                        **{key: {"noul": 0.1} for key in QUESTIONS if key != "stance"},
                    },
                }

        result = JevClassifier(Fake(0.126)).classify({})
        self.assertAlmostEqual(sum(result.stance_distribution.values()), 1)
        with self.assertRaises(ValueError):
            JevClassifier(Fake(0.2)).classify({})


if __name__ == "__main__":
    unittest.main()
