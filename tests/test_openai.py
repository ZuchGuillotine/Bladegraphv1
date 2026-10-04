import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bladegraph.cost_ledger import Ledger
from bladegraph.providers import Provider

ENV = {
    "OPENAI_API_KEY": "test-only",
    "WORKER_PROXY_AUTH": "0",
    "WORKER_PROVIDER": "openai",
    "WORKER_MODEL": "gpt-6.1-sol",
    "WORKER_INPUT_USD_PER_MILLION": "2",
    "WORKER_OUTPUT_USD_PER_MILLION": "10",
    "WORKER_MAX_OUTPUT_TOKENS": "6500",
    "WORKER_REASONING_EFFORT": "medium",
    "WORKER_PROVIDER_ORDER": "",
    "WORKER_USAGE_ACCOUNTING": "0",
}


class OpenAITests(unittest.TestCase):
    @patch.dict(os.environ, ENV)
    def test_direct_request_settles_on_configured_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            requests = []

            def transport(body):
                requests.append(json.loads(body))
                return {
                    "model": "gpt-6.1-sol",
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"op":"wait"}'}}],
                    "usage": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 300,
                        "completion_tokens_details": {"reasoning_tokens": 200},
                    },
                }

            p = Provider(ledger, "worker_agents", transport=transport)
            self.assertEqual(p.chat("Only JSON", {"safe": "state"}), {"op": "wait"})
            self.assertEqual(p.endpoint, "https://api.openai.com/v1/chat/completions")
            sent = requests[0]
            self.assertEqual(sent["model"], "gpt-6.1-sol")
            self.assertEqual(sent["max_completion_tokens"], 6500)
            self.assertEqual(sent["reasoning_effort"], "medium")
            self.assertEqual(sent["response_format"], {"type": "json_object"})
            for absent in ("max_tokens", "temperature", "plugins", "reasoning", "usage", "provider"):
                self.assertNotIn(absent, sent)
            self.assertNotIn("test-only", json.dumps(requests))
            self.assertAlmostEqual(p.records[0]["usd"], 0.005)
            self.assertEqual(p.records[0]["cost_basis"], "configured prices")
            self.assertEqual(p.records[0]["reasoning_tokens"], 200)

    @patch.dict(os.environ, {**ENV, "WORKER_USAGE_ACCOUNTING": "1"})
    def test_openrouter_only_options_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            with self.assertRaisesRegex(ValueError, "OpenRouter options"):
                Provider(ledger, "worker_agents")


if __name__ == "__main__":
    unittest.main()
