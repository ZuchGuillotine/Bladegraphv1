import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from bladegraph.bootstrap_builder import bootstrap
from bladegraph.cost_ledger import BudgetExceeded, Ledger
from bladegraph.event_log import EventLog
from bladegraph.experiment_runner import simulate
from bladegraph.graph_projector import Graph
from bladegraph.intervention_controller import Controller, Policy
from bladegraph.jev_classifier import FixtureClassifier, JevClassifier, QUESTIONS
from bladegraph.models import Artifact, Classification, Review
from bladegraph.quarantine_engine import admissible, quarantine, release, worker_artifact
from bladegraph.respawn_manager import respawn
from bladegraph.reviewer import FixtureReviewer
from bladegraph.risk_engine import recompute
from bladegraph.swarm_runtime import Runtime


class CoreTests(unittest.TestCase):
    def runtime(self, arm="E", classifier=None, reviewer=None):
        log = EventLog()
        self.addCleanup(log.close)
        return Runtime(
            log,
            Controller(
                classifier or FixtureClassifier(),
                reviewer or FixtureReviewer(),
                Policy.legacy(automatic_culling=True),
                arm,
            ),
        )

    def artifact(self, rt, key, deps=None, verified=False, risk=0):
        rt.emit(
            "artifact.created",
            payload=asdict(
                Artifact(
                    key,
                    "environment",
                    {"kind": "note", "text": key},
                    derived_from=deps or [],
                    independent_validation=verified,
                    verification_state="VERIFIED" if verified else "UNVERIFIED",
                    local_content_risk=risk,
                )
            ),
        )
        recompute(rt.graph)

    def test_thirteen_initial_agents(self):
        self.assertEqual(len(self.runtime().active_agents()), 13)

    def test_read_is_not_adoption_or_contamination(self):
        rt = self.runtime()
        self.artifact(rt, "bad", risk=0.99)
        agent = rt.active_agents()[0]
        rt.step(agent, {"op": "read", "artifact_id": "bad"})
        self.assertEqual(agent.exposure_count, 1)
        self.assertLess(agent.behavioral_risk, 0.1)
        self.assertEqual(agent.adoption_evidence, [])
        self.assertNotEqual(agent.state, "CULLED")

    def test_risk_follows_only_derivation(self):
        rt = self.runtime()
        self.artifact(rt, "bad", risk=0.9)
        self.artifact(rt, "child", ["bad"])
        self.artifact(rt, "rebuttal")
        rt.emit("relation.created", payload={"source": "rebuttal", "target": "bad", "kind": "CONTRADICTS"})
        recompute(rt.graph)
        self.assertEqual(rt.graph.artifacts["child"].effective_risk, 0.9)
        self.assertEqual(rt.graph.artifacts["rebuttal"].effective_risk, 0)

    def test_quarantine_preserves_independent_validation(self):
        rt = self.runtime()
        self.artifact(rt, "bad")
        self.artifact(rt, "unverified", ["bad"])
        self.artifact(rt, "checked", ["bad"], verified=True)
        self.artifact(rt, "checked-child", ["checked"])
        ids = quarantine(rt.graph, rt.emit, "bad", "e0000001")
        self.assertEqual(ids, ["bad", "unverified"])
        self.assertTrue(admissible(rt.graph, "checked-child"))
        self.assertNotIn("content", worker_artifact(rt.graph, "bad"))

    def test_cull_requires_review_confirmation(self):
        class Uncertain:
            def review(self, context, result):
                return Review("UNCERTAIN", "AMBIGUOUS", [context["event_id"]], [], "publish", "", 0.5, "RESTRICT")

        rt = self.runtime(reviewer=Uncertain())
        agent = rt.active_agents()[0]
        rt.step(agent, {"op": "read", "artifact_id": "source-task-00"})
        rt.step(
            agent,
            {
                "op": "publish",
                "content": {"kind": "claim", "task_id": agent.task_id, "value": 0},
                "derived_from": ["source-task-00"],
            },
        )
        self.assertEqual(agent.state, "RESTRICTED")

    def test_cull_and_respawn_revokes_old_identity(self):
        rt = self.runtime()
        old = rt.active_agents()[0]
        rt.step(old, {"op": "read", "artifact_id": "source-task-00"})
        rt.step(
            old,
            {
                "op": "publish",
                "content": {"kind": "claim", "task_id": old.task_id, "value": 0},
                "derived_from": ["source-task-00"],
            },
        )
        self.assertEqual(old.state, "CULLED")
        new, packet = respawn(rt, old, recover=True)
        self.assertEqual(new.reads, set())
        self.assertEqual(rt.contexts[new.key], [])
        self.assertLess(len(json.dumps(packet)), 1800)
        self.assertNotIn("threshold", json.dumps(packet))
        self.assertEqual(rt.step(old, {"op": "wait"})["status"], "unavailable")
        self.assertEqual(len(rt.active_agents()), 13)

    def test_worker_cannot_read_repository_or_monitor(self):
        rt = self.runtime()
        agent = rt.active_agents()[0]
        for action in (
            {"op": "shell", "command": "cat .env"},
            {"op": "read", "artifact_id": "../../.env"},
            {"op": "query", "task_id": "monitor"},
            {"op": "wait", "agent_id": "controller"},
        ):
            self.assertEqual(rt.step(agent, action)["status"], "unavailable")
        state = json.dumps(rt.worker_state(agent))
        for text in ("github", "QUESTIONS", "quarantine", "policy", "api_key", "classifier"):
            self.assertNotIn(text, state.lower())

    def test_quarantine_scrubs_cached_raw_content(self):
        rt = self.runtime()
        self.artifact(rt, "secret-route")
        agent = rt.active_agents()[0]
        rt.step(agent, {"op": "read", "artifact_id": "secret-route"})
        quarantine(rt.graph, rt.emit, "secret-route", "e0000001")
        self.assertNotIn('"text": "secret-route"', json.dumps(rt.worker_state(agent)))

    def test_no_forged_provenance_or_peer_validation(self):
        rt = self.runtime()
        agent = rt.active_agents()[0]
        result = rt.step(
            agent,
            {
                "op": "publish",
                "content": {"kind": "claim", "task_id": agent.task_id, "value": 1},
                "derived_from": ["source-task-00"],
            },
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(rt.step(agent, {"op": "validate", "artifact_id": "source-task-00"})["status"], "unavailable")

    def test_cannot_poison_verification_by_submitting_source(self):
        rt = self.runtime()
        agent = rt.active_agents()[0]
        rt.step(agent, {"op": "read", "artifact_id": "source-task-00"})
        self.assertEqual(rt.step(agent, {"op": "submit", "artifact_id": "source-task-00"})["status"], "unavailable")
        self.assertEqual(rt.graph.artifacts["source-task-00"].verification_state, "VERIFIED")
        self.assertIn("only claims", json.dumps(rt.worker_state(agent)))

    def test_quarantine_is_reversible_but_not_through_blocked_parent(self):
        rt = self.runtime()
        self.artifact(rt, "parent")
        self.artifact(rt, "child", ["parent"])
        quarantine(rt.graph, rt.emit, "parent", "e0000001")
        self.assertEqual(release(rt.graph, rt.emit, "child", "e0000001"), [])
        self.assertEqual(release(rt.graph, rt.emit, "parent", "e0000001"), ["parent", "child"])
        self.assertTrue(admissible(rt.graph, "child"))

    def test_hysteresis_requires_three_low_risk_observations(self):
        rt = self.runtime()
        agent = rt.active_agents()[0]
        rt.emit("agent.state", agent=agent, payload={"state": "WATCH"})
        for i in range(2):
            rt.step(agent, {"op": "wait"})
            self.assertEqual(agent.state, "WATCH")
        rt.step(agent, {"op": "wait"})
        self.assertEqual(agent.state, "NORMAL")

    def test_review_evidence_ids_must_exist(self):
        review = Review("CONFIRM", "ADOPTING", ["invented"], [], "publish", "accuracy", 0.99, "CULL")
        with self.assertRaises(ValueError):
            review.validate({"real"}, set())

    def test_monitor_failure_restricts_and_hides_write(self):
        class Broken:
            def classify(self, context):
                raise RuntimeError("offline")

        rt = self.runtime(classifier=Broken())
        agent = rt.active_agents()[0]
        result = rt.step(
            agent, {"op": "message", "content": {"kind": "note", "text": "unreviewed"}, "derived_from": []}
        )
        self.assertEqual(agent.state, "RESTRICTED")
        self.assertNotIn("content", result)

    def test_report_cannot_directly_sanction_peer(self):
        rt = self.runtime()
        self.artifact(rt, "suspect")
        agent, peer = rt.active_agents()[:2]
        rt.step(agent, {"op": "read", "artifact_id": "suspect"})
        rt.step(agent, {"op": "report", "artifact_id": "suspect", "summary": "Please inspect."})
        self.assertEqual(peer.state, "NORMAL")
        self.assertEqual(agent.adoption_evidence, [])

    def test_log_and_graph_replay(self):
        rt = self.runtime()
        agent = rt.active_agents()[0]
        rt.step(agent, {"op": "calculate", "artifact_id": "source-task-00"})
        rt.log.verify()
        self.assertEqual(Graph.replay(rt.log.events()).snapshot(), rt.graph.snapshot())
        with self.assertRaises(Exception):
            rt.log.db.execute("UPDATE events SET body='{}'")

    def test_invalid_probability_rejected(self):
        c = FixtureClassifier().classify({})
        for value in (float("nan"), 1.1, -1, True):
            with self.assertRaises(ValueError):
                replace(c, confidence=value)

    def test_budget_persists_between_runs_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.sqlite"
            first = Ledger(path, "a", 1)
            first.reserve("Jev", 0.75)
            with self.assertRaises(BudgetExceeded):
                first.reserve("Jev", 0.5)
            second = Ledger(path, "b", 1)
            self.assertEqual(second.summary()["total_usd"], 0.75)
            self.assertEqual(second.summary()["unsettled_reservations"], 1)
            first.db.close()
            second.db.close()

    def test_simulation_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            a = simulate(p / "a", "E", 7)
            b = simulate(p / "b", "E", 7)
            self.assertEqual(a, b)
            self.assertEqual((p / "a/events.jsonl").read_bytes(), (p / "b/events.jsonl").read_bytes())

    def test_jev_contract_with_mock_transport(self):
        class FakeProvider:
            model = "jev-pinned-test"

            def call(self, payload):
                self.payload = payload
                c = FixtureClassifier().classify({})
                return {
                    "model": self.model,
                    "answers": {
                        "stance": {"probabilities": c.stance_distribution, "confidence": 0.98},
                        **{key: {"noul": 0.1} for key in QUESTIONS if key != "stance"},
                    },
                }

        provider = FakeProvider()
        c = JevClassifier(provider).classify({"message": "test"})
        self.assertEqual(c.model, "jev-pinned-test")
        self.assertEqual(provider.payload["questions"]["stance"]["type"], "choice")


if __name__ == "__main__":
    unittest.main()
