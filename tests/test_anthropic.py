import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bladegraph.providers import Provider
from bladegraph.cost_ledger import Ledger


class AnthropicTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "ANTHROPIC_API_KEY": "test-only",
            "REVIEWER_PROXY_AUTH": "0",
            "REVIEWER_PROVIDER": "anthropic",
            "REVIEWER_MODEL": "claude-sonnet-4-6",
            "REVIEWER_INPUT_USD_PER_MILLION": "3",
            "REVIEWER_OUTPUT_USD_PER_MILLION": "15",
        },
    )
    def test_direct_request_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            requests = []

            def transport(body):
                requests.append(json.loads(body))
                return {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"op":"wait"}'}],
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                }

            p = Provider(ledger, "reviewer", transport=transport)
            self.assertEqual(p.chat("Only JSON", {"safe": "state"}), {"op": "wait"})
            self.assertEqual(p.endpoint, "https://api.anthropic.com/v1/messages")
            self.assertEqual(requests[0]["system"], "Only JSON")
            self.assertNotIn("response_format", requests[0])
            self.assertNotIn("temperature", requests[0])
            self.assertNotIn("test-only", json.dumps(requests))
            self.assertAlmostEqual(p.records[0]["usd"], 0.00021)

    @patch.dict(
        os.environ,
        {
            "BLADEGRAPH_ANTHROPIC_API_KEY": "bladegraph-only",
            "ANTHROPIC_API_KEY": "",
            "WORKER_PROXY_AUTH": "0",
            "WORKER_PROVIDER": "anthropic",
            "WORKER_MODEL": "claude-opus-5-5",
            "WORKER_INPUT_USD_PER_MILLION": "4",
            "WORKER_OUTPUT_USD_PER_MILLION": "20",
        },
    )
    def test_bladegraph_key_name_used_when_reserved_name_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            self.assertEqual(Provider(ledger, "worker_agents").key, "bladegraph-only")
            os.environ["ANTHROPIC_API_KEY"] = "reserved-name"
            self.assertEqual(Provider(ledger, "worker_agents").key, "bladegraph-only")
            del os.environ["BLADEGRAPH_ANTHROPIC_API_KEY"]
            self.assertEqual(Provider(ledger, "worker_agents").key, "reserved-name")
            os.environ["ANTHROPIC_API_KEY"] = ""
            with self.assertRaisesRegex(ValueError, "BLADEGRAPH_ANTHROPIC_API_KEY"):
                Provider(ledger, "worker_agents")

    @patch.dict(os.environ, {"REVIEWER_PROXY_AUTH": "1", "REVIEWER_PROVIDER": "anthropic"})
    def test_anthropic_proxy_auth_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            with self.assertRaises(ValueError):
                Provider(ledger, "reviewer")

    @patch.dict(
        os.environ,
        {
            "REVIEWER_PROXY_AUTH": "1",
            "REVIEWER_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "",
            "REVIEWER_MODEL": "test/model-v1",
            "REVIEWER_INPUT_USD_PER_MILLION": "1",
            "REVIEWER_OUTPUT_USD_PER_MILLION": "1",
        },
    )
    def test_proxy_auth_no_local_key_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            p = Provider(ledger, "reviewer")
            self.assertTrue(p.proxy_auth)
            self.assertEqual(p.key, "")
