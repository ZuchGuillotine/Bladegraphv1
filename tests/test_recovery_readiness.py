import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bladegraph.intervention_controller import Controller, Policy
from bladegraph.event_log import EventLog
from bladegraph.operational import OperationalRuntime
from bladegraph.reviewer import FixtureReviewer, ModelReviewer
from bladegraph.respawn_manager import respawn
from bladegraph.researcher_actions import force_cull, run_scripted_fault
from bladegraph.recovery_metrics import recovery_delivery
from bladegraph.providers import Provider, ProviderUnavailable
from bladegraph.cost_ledger import Ledger
from bladegraph.graph_projector import Graph
from bladegraph.risk_engine import recompute
from scripts.run_study import sanction_metrics
from scripts import run_series
from test_series import AuditAware
from test_restore_ledger import make_ledger, restore_ledger


class RecoveryReadinessTests(unittest.TestCase):
    def test_every_live_manifest_command_parses_before_any_spend(self):
        manifest = run_series.load_manifest(run_series.ROOT / "experiments/series/series5-recovery-glm53-v6.json")
        with tempfile.TemporaryDirectory() as tmp:
            run_series.validate_commands(manifest, Path(tmp))
            ordinary = next(e for e in manifest["episodes"] if "ordinary" in e.get("args", []))
            ordinary["args"] += ["--fault-repeat", "1"]
            with self.assertRaisesRegex(ValueError, "requires --condition scripted-fault"):
                run_series.validate_commands(manifest, Path(tmp))

    def test_every_series_manifest_environment_passes_the_credential_guard(self):
        for path in sorted((run_series.ROOT / "experiments/series").glob("*.json")):
            with self.subTest(manifest=path.name):
                run_series.manifest_environment(run_series.load_manifest(path))
        with self.assertRaisesRegex(ValueError, "never credentials"):
            run_series.manifest_environment({"environment": {"note": "operator text"}})

    def runtime(self, arm="E", auto=True):
        log = EventLog()
        self.addCleanup(log.close)
        rt = OperationalRuntime(
            log, Controller(AuditAware(), FixtureReviewer(), Policy(automatic_culling=auto), arm), seed=71
        )
        rt.deadline = 24
        rt.set_submission_cycle(8)
        rt.configure_two_stage(update_cycle=10, final_cycle=16, sites=[4, 5], flip="flip")
        rt.respawn_credits = 2
        rt.respawn_context = "ids"
        return rt

    def test_transport_outage_aborts_full_runner_after_three_reserved_calls(self):
        from scripts import run_workflow

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "spend.sqlite"
            out = root / "run"
            seed = Ledger(ledger, "fixture", 1)
            seed.db.close()
            calls = []

            def unavailable(body):
                calls.append(body)
                raise ProviderUnavailable(error_kind="gaierror", error_number=-2)

            env = {
                "BLADEGRAPH_LEDGER": str(ledger),
                "OPENROUTER_API_KEY": "fixture-only",
                "TYPESAFE_API_KEY": "fixture-only",
            }
            for prefix in ("WORKER", "REVIEWER", "JEV"):
                env.update(
                    {
                        prefix + "_MODEL": "fixture-pinned",
                        prefix + "_INPUT_USD_PER_MILLION": "1",
                        prefix + "_OUTPUT_USD_PER_MILLION": "1",
                    }
                )
            argv = [
                "run_workflow.py",
                "--output",
                str(out),
                "--cycles",
                "16",
                "--submission-cycle",
                "8",
                "--restart-cycle",
                "0",
                "--budget",
                "1",
            ]
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(sys, "argv", argv),
                patch.object(run_workflow, "load_env"),
                patch.object(
                    run_workflow,
                    "Provider",
                    side_effect=lambda ledger, component: Provider(ledger, component, transport=unavailable),
                ),
            ):
                with self.assertRaises(ProviderUnavailable):
                    run_workflow.main()
            self.assertEqual(len(calls), 3)
            self.assertEqual(json.loads((out / "termination.json").read_text())["status"], "failed")
            metrics = json.loads((out / "metrics.json").read_text())
            self.assertEqual(metrics["failed_worker_calls"], 3)
            self.assertEqual(metrics["total_api_usd"], 0)
            self.assertGreater(metrics["retained_reservation_usd"], 0)
            failure = metrics["provider_transport_failures"][0]
            audit = json.loads((out / "provider-responses" / (failure["audit_id"] + ".json")).read_text())
            self.assertEqual(audit["transport_failure"]["error_kind"], "gaierror")
            self.assertNotIn("response", audit)
            self.assertEqual([f["fatal"] for f in metrics["provider_transport_failures"]], [False, False, True])
            self.assertTrue(out.with_suffix(".tar.gz").exists())

    def test_success_resets_transport_failure_counter_without_replaying_a_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "spend.sqlite", "fixture", 1)
            self.addCleanup(ledger.db.close)
            outcomes = [
                ProviderUnavailable(http_status=520),
                {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
                ProviderUnavailable(http_status=520),
            ]

            def transport(body):
                result = outcomes.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            env = {
                "OPENROUTER_API_KEY": "fixture-only",
                "WORKER_MODEL": "fixture-pinned",
                "WORKER_INPUT_USD_PER_MILLION": "1",
                "WORKER_OUTPUT_USD_PER_MILLION": "1",
            }
            with patch.dict(os.environ, env, clear=True):
                provider = Provider(ledger, "worker_agents", transport=transport)
            for i in range(3):
                payload = {"max_tokens": 64, "messages": [{"role": "user", "content": str(i)}]}
                if i == 1:
                    provider.call(payload)
                else:
                    with self.assertRaises(ProviderUnavailable) as caught:
                        provider.call(payload)
                    self.assertFalse(caught.exception.fatal)
            self.assertEqual([f["consecutive_failures"] for f in provider.failure_records], [1, 1])
            self.assertEqual(ledger.db.execute("select count(*) from costs").fetchone()[0], 3)

    def test_mid_publication_monitor_abort_preserves_graph_replay(self):
        rt = self.runtime()
        w = rt.active_agents()[4]
        measurement = rt.step(w, {"op": "evaluate", "model": "m0", "site": 4, "cohort": "all"})
        rt.emit(
            "artifact.assessed", agent=w, payload={"artifact_id": measurement["id"], "risk": 0.2, "conflict": False}
        )
        recompute(rt.graph)

        class Unavailable:
            def classify(self, *args):
                raise ProviderUnavailable(http_status=503)

        rt.controller.shadow_classifier = Unavailable()
        with self.assertRaises(ProviderUnavailable):
            rt.step(w, {"op": "publish_evaluation", "artifact_id": measurement["id"]})
        self.assertEqual(Graph.replay(rt.log.events()).snapshot(), rt.graph.snapshot())

    def test_export_failure_still_retains_forensic_bundle_and_failed_status(self):
        from scripts.run_workflow import preserve_failed_export

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "failed"
            out.mkdir()
            rt = self.runtime()
            ledger = Ledger(root / "spend.sqlite", "fixture", 1)
            self.addCleanup(ledger.db.close)
            from types import SimpleNamespace

            provider = SimpleNamespace(records=[], failure_records=[])
            preserve_failed_export(rt, out, ledger, [provider], AssertionError("fixture replay mismatch"))
            self.assertEqual(json.loads((out / "termination.json").read_text())["status"], "failed")
            self.assertTrue((out / "events.jsonl").exists())
            self.assertTrue((out / "live-graph-forensics.json").exists())
            self.assertTrue(out.with_suffix(".tar.gz").exists())
            self.assertFalse((out / "episode-complete.json").exists())

    def test_monitor_and_reviewer_transport_outages_propagate(self):
        class Unavailable:
            def classify(self, *args):
                raise ProviderUnavailable(http_status=503)

            def review(self, *args):
                raise ProviderUnavailable(http_status=503)

        for stage in ("active", "shadow", "reviewer"):
            with self.subTest(stage=stage):
                rt = self.runtime()
                w = rt.active_agents()[4]
                if stage == "reviewer":
                    rt.tick = 3
                    run_scripted_fault(rt, w, 0, 3)
                    rt.controller.reviewer = Unavailable()
                    rt.tick = 4
                    with self.assertRaises(ProviderUnavailable):
                        run_scripted_fault(rt, w, 1, 4)
                    event = "review.failed"
                else:
                    if stage == "active":
                        rt.controller.classifier = Unavailable()
                    else:
                        rt.controller.shadow_classifier = Unavailable()
                    with self.assertRaises(ProviderUnavailable):
                        rt.step(w, {"op": "evaluate", "model": "m0", "site": 4, "cohort": "all"})
                    event = "monitor.failed" if stage == "active" else "monitor.shadow_failed"
                self.assertEqual(sum(e["event_type"] == event for e in rt.log.events()), 1)

    def publish(self, rt, worker, model="m0"):
        measurement = rt.step(worker, {"op": "evaluate", "model": model, "site": 4, "cohort": "all"})
        card = rt.step(worker, {"op": "publish_evaluation", "artifact_id": measurement["id"]})
        return measurement["id"], card["id"]

    def test_stale_bootstrap_removed_but_history_is_traversable(self):
        rt = self.runtime()
        w = rt.active_agents()[4]
        old, card = self.publish(rt, w)
        rt.tick = 10
        rt.apply_site_update()
        fresh, packet = force_cull(rt, w, "fixture", recover=True)
        self.assertNotIn(old, packet["approved_node_ids"])
        self.assertNotIn(card, packet["approved_node_ids"])
        listing = rt.step(fresh, {"op": "query", "scope": "site-4"})
        stale = next(n for n in listing["nodes"] if n["id"] == old)
        self.assertFalse(stale["current"])
        self.assertEqual(stale["data_version"], 1)
        read = rt.step(fresh, {"op": "read", "artifact_id": old})
        self.assertFalse(read["current"])
        self.assertIn(old, fresh.reads)
        self.assertLessEqual(len(json.dumps(packet)), 1800)

    def test_work_metric_deduplicates_and_uses_deadline_applicability(self):
        rt = self.runtime(arm="A")
        w = rt.active_agents()[4]
        rt.tick = 4
        evaluation, first = self.publish(rt, w)
        rt.step(w, {"op": "publish_evaluation", "artifact_id": evaluation})
        config = {
            "condition": "scripted-fault",
            "scripted_fault": {"worker": "worker-04", "cycles": [3, 4, 5]},
            "arm": "A",
        }
        metrics = sanction_metrics(rt, list(rt.log.events()), config)["post_intervention_work"]
        self.assertEqual(metrics["truthful_current_cards_after"], 1)
        rt.tick = 10
        rt.apply_site_update()
        metrics = sanction_metrics(rt, list(rt.log.events()), config)["post_intervention_work"]
        self.assertEqual(metrics["truthful_current_cards_after"], 0)
        self.assertEqual(metrics["historical_truthful_publications_after"], 2)

    def test_four_arm_loop_has_equal_replacement_resources_and_distinct_briefs(self):
        results = {}
        for arm in ("Q", "C", "R0", "RG"):
            rt = self.runtime(arm="D" if arm in ("Q", "C") else "E", auto=arm != "Q")
            w = rt.active_agents()[4]
            self.publish(rt, w)
            rt.tick = 3
            run_scripted_fault(rt, w, 0, 3)
            rt.tick = 4
            run_scripted_fault(rt, w, 1, 4)
            rt.tick = 7
            rt.worker_state(w)
            run_scripted_fault(rt, w, 1, 7, repeat=1)
            if arm in ("R0", "RG"):
                fresh, packet = respawn(rt, w, recover=arm == "RG")
                results[arm] = packet
                # Locate predecessor evidence using existing graph operations and publish useful work.
                rt.tick = 8
                model = next(m for m in rt.profiles if m not in ("m0", rt.trap_label()))
                measurement = rt.step(fresh, {"op": "evaluate", "model": model, "site": 4, "cohort": "all"})
                self.assertIn("id", measurement)
                rt.tick = 9
                rt.step(fresh, {"op": "publish_evaluation", "artifact_id": measurement["id"]})
            config = {
                "condition": "scripted-fault",
                "scripted_fault": {"worker": "worker-04", "cycles": [3, 4, 5], "repeat": 1},
                "automatic_culling": arm != "Q",
                "arm": rt.controller.arm,
                "recovery_mode": "graph" if arm == "RG" else "blank",
                "respawn_credits": 2,
            }
            delivery = recovery_delivery(rt, list(rt.log.events()), config)
            self.assertTrue(delivery["delivered"], (arm, delivery))
            self.assertEqual(delivery["culled"], arm != "Q")
            if arm in ("R0", "RG"):
                metrics = sanction_metrics(rt, list(rt.log.events()), config)
                self.assertGreater(metrics["replacement_productivity"][0]["distinct_useful_units"], 0)
        self.assertEqual(results["R0"]["evaluation_credits_remaining"], results["RG"]["evaluation_credits_remaining"])
        self.assertFalse(results["R0"]["approved_node_ids"])
        self.assertTrue(results["RG"]["approved_node_ids"])

    def test_gate_rejects_failed_or_foreign_report_and_metrics_only_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "metrics.json").write_text("{}")
            self.assertFalse(run_series.completed_episode(base))
            for report in ({"binding": "same", "accepted": False}, {"binding": "other", "accepted": True}):
                with self.assertRaises(ValueError):
                    run_series.require_gate(report, "same", base)
            profile = base / "accepted-policy.json"
            profile.write_text("{}")
            report = {
                "binding": "same",
                "accepted": True,
                "profile_hash": run_series.file_hash(profile),
                "calibration_hashes": {},
                "verdict": {"pass": True},
                "proposal": {"status": "proposed", "positive_controls_verified": True},
            }
            run_series.require_gate(report, "same", base)
            profile.write_text('{"changed":true}')
            with self.assertRaises(ValueError):
                run_series.require_gate(report, "same", base)

    def test_gate_requires_all_calibration_and_auto_never_drops_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = {
                "model": "test",
                "worker_prices": [1, 1],
                "common": [],
                "episodes": [{"name": "missing", "role": "calibration"}],
            }
            with self.assertRaises(ValueError):
                run_series.run_gate(m, Path(tmp), {"name": "gate"}, "binding")
            cmd, profile = run_series.build_command(
                m, {"policy_profile": "auto", "args": []}, Path(tmp) / "run", Path(tmp) / "accepted-policy.json"
            )
            self.assertEqual(profile, "test")
            self.assertIn("--policy-profiles-file", cmd)

    def test_ledger_union_is_idempotent_and_conflicts_are_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.sqlite"
            b = root / "b.sqlite"
            dest = root / "merged.sqlite"
            make_ledger(a, [("base", 1, "settled"), ("left", 2, "reserved")])
            make_ledger(b, [("base", 1, "settled"), ("right", 3, "settled")])
            result = restore_ledger.merge_ledgers([a, b], dest)
            self.assertEqual((result["rows"], result["total_usd"], result["unsettled_reservations"]), (3, 6, 1))
            self.assertTrue(restore_ledger.merge_ledgers([b, a], dest)["kept_existing"])
            conflict = root / "conflict.sqlite"
            make_ledger(conflict, [("base", 9, "settled")])
            with self.assertRaises(ValueError):
                restore_ledger.merge_ledgers([conflict], dest)
            self.assertEqual(restore_ledger.ledger_stats(dest), (3, 6, 1))


if __name__ == "__main__":
    unittest.main()
