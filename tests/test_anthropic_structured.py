"""Anthropic backend structured output (2026-10-12): the worker reply is constrained to the protocol
schema, effort passes through output_config, nullable optional fields are stripped, and a cut-off
turn is counted as truncated."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bladegraph.cost_ledger import Ledger
from bladegraph.cooperative import FIELDS
from bladegraph.providers import WORKER_ACTION_SCHEMA, MalformedResponse, Provider

ENV = {
    "BLADEGRAPH_ANTHROPIC_API_KEY": "test-only",
    "WORKER_PROXY_AUTH": "0",
    "WORKER_PROVIDER": "anthropic",
    "WORKER_MODEL": "claude-sonnet-5-5",
    "WORKER_INPUT_USD_PER_MILLION": "2",
    "WORKER_OUTPUT_USD_PER_MILLION": "10",
    "WORKER_MAX_OUTPUT_TOKENS": "6500",
    "WORKER_REASONING_EFFORT": "low",
    "WORKER_PROVIDER_ORDER": "",
    "WORKER_USAGE_ACCOUNTING": "0",
}


def walk(schema):
    yield schema
    for key in ("properties", "items", "anyOf"):
        value = schema.get(key)
        if isinstance(value, dict):
            for sub in value.values() if key == "properties" else [value]:
                yield from walk(sub)
        elif isinstance(value, list):
            for sub in value:
                yield from walk(sub)


class AnthropicStructuredOutputTests(unittest.TestCase):
    def test_schema_mirrors_the_protocol_and_is_strict(self):
        ops = {}
        for variant in WORKER_ACTION_SCHEMA["anyOf"]:
            op = variant["properties"]["op"]["const"]
            ops[op] = set(variant["properties"])
            self.assertEqual(set(variant["required"]), set(variant["properties"]), op)
        self.assertEqual(ops, {op: set(fields) for op, fields in FIELDS.items()})
        for node in walk(WORKER_ACTION_SCHEMA):
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)

    @patch.dict(os.environ, ENV)
    def test_request_carries_structured_output_and_effort_and_strips_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)
            requests = []

            def transport(body):
                requests.append(json.loads(body))
                return {
                    "model": "claude-sonnet-5-5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"op":"query","scope":"board","cursor":null}'}],
                    "usage": {"input_tokens": 1000, "output_tokens": 50},
                }

            p = Provider(ledger, "worker_agents", transport=transport)
            self.assertEqual(p.chat("Only JSON", {"safe": "state"}), {"op": "query", "scope": "board"})
            sent = requests[0]
            self.assertEqual(sent["output_config"]["format"], {"type": "json_schema", "schema": WORKER_ACTION_SCHEMA})
            self.assertEqual(sent["output_config"]["effort"], "low")
            self.assertEqual(sent["max_tokens"], 6500)
            for absent in ("temperature", "plugins", "response_format", "reasoning", "thinking"):
                self.assertNotIn(absent, sent)
            self.assertNotIn("test-only", json.dumps(requests))

    @patch.dict(os.environ, ENV)
    def test_cut_off_turn_is_malformed_with_the_anthropic_stop_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite", "test")
            self.addCleanup(ledger.db.close)

            def transport(body):
                return {
                    "model": "claude-sonnet-5-5",
                    "stop_reason": "max_tokens",
                    "content": [{"type": "text", "text": '{"op":"wa'}],
                    "usage": {"input_tokens": 1000, "output_tokens": 6500},
                }

            p = Provider(ledger, "worker_agents", transport=transport)
            with self.assertRaises(MalformedResponse) as caught:
                p.chat("Only JSON", {"safe": "state"})
            self.assertEqual(caught.exception.finish_reason, "max_tokens")


if __name__ == "__main__":
    unittest.main()
