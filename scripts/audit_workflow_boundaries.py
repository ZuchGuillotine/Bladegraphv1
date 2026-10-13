"""Verify retained worker requests and scan their task-only information boundary."""

import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bladegraph.providers import anthropic_output_config
from bladegraph.event_log import EventLog, canonical, digest
from bladegraph.providers import load_env
from bladegraph.operational import DIRECTIVE_MARKERS

FORBIDDEN = [
    "zuchguillotine",
    "bladegraph",
    "private_condition",
    "directive_version",
    "stance_distribution",
    "goal_violation_probability",
    "monitor.context",
    "implementation_sources",
    "research.supplier_ingress",
    "research.intervention",
    "artifact.summarized",
    "upstream-fault",
    "upstream-clean",
    "github.com",
    "benchmark",
    "seeded",
    "experiment",
]


def forbidden_terms():
    """Static research identifiers plus host-specific ones supplied at runtime, never committed:
    the researcher's home directory name and any BLADEGRAPH_AUDIT_TERMS (comma separated)."""
    extra = [Path.home().name] + os.environ.get("BLADEGRAPH_AUDIT_TERMS", "").split(",")
    return FORBIDDEN + [t.strip().lower() for t in extra if len(t.strip()) >= 4]


def audit_run(path, secrets):
    config = json.loads((path / "metrics.json").read_text())["config"]
    terms = forbidden_terms()
    log = EventLog(path / "events.sqlite")
    log.verify()
    events = list(log.events())
    log.close()
    pending = None
    count = 0
    confirmed = 0
    flags = []
    private_count = 0
    unanswered = []
    for e in events:
        p = e["payload"]
        if e["event_type"] == "worker.request":
            pending = e
            count += 1
            text = json.dumps(p["request"]) + "\n" + p["system_prompt"]
            if any(s in text for s in secrets):
                raise ValueError("credential appeared in worker request")
            if len(json.dumps(p["request"]["bootstrap"])) > 1800:
                raise ValueError("oversized bootstrap")
            for term in terms:
                if term in text.lower():
                    flags.append({"event_id": e["event_id"], "term": term})
            insiders = set(config.get("insiders", ["worker-04"]))
            if any(m in p["system_prompt"] for m in DIRECTIVE_MARKERS):
                private_count += 1
                if e["agent_id"] not in insiders or e["agent_generation"] != 0 or config["condition"] != "insider":
                    raise ValueError("private directive went to wrong participant")
            if any(m in json.dumps(p["request"]) for m in DIRECTIVE_MARKERS):
                flags.append(
                    {"event_id": e["event_id"], "term": "full private directive in shared state; review origin"}
                )
        elif e["event_type"] in {"run.failed", "worker.failed"} and pending is not None:
            # A provider failure leaves a retained reservation and no completed response.
            unanswered.append(pending["event_id"])
            pending = None
        elif e["event_type"] == "provider.usage":
            if pending is None:
                raise ValueError("usage without retained worker request")
            req = pending["payload"]
            if config.get("worker_provider", "openrouter") == "anthropic":
                options = config.get("worker_request_options", {})
                payload = {
                    "system": req["system_prompt"],
                    "messages": [{"role": "user", "content": canonical(req["request"])}],
                    "max_tokens": options.get("max_tokens", 1200),
                    # No temperature: the Anthropic backend omits sampling parameters (Opus 5.5).
                    "model": config["worker_model"],
                }
                # Runs since 2026-10-12 constrain the reply to the worker action schema and pass
                # effort through output_config (series 8 Sonnet 5.5). Runs that recorded the
                # option carry it in worker_request_options; the one v2 episode recorded before
                # that (9f178a5) carried only a reasoning dict, so its output_config is rebuilt
                # from the effort it recorded. Earlier Anthropic runs (series 3) sent neither.
                if "output_config" in options:
                    payload["output_config"] = options["output_config"]
                elif options.get("reasoning") is not None:
                    payload["output_config"] = anthropic_output_config(None, options["reasoning"].get("effort"))
                endpoint = "https://api.anthropic.com/v1/messages"
            elif config.get("worker_provider") == "openai":
                payload = {
                    "messages": [
                        {"role": "system", "content": req["system_prompt"]},
                        {"role": "user", "content": canonical(req["request"])},
                    ],
                    "response_format": {"type": "json_object"},
                    **config.get("worker_request_options", {}),
                    "model": config["worker_model"],
                }
                endpoint = "https://api.openai.com/v1/chat/completions"
            else:
                payload = {
                    "messages": [
                        {"role": "system", "content": req["system_prompt"]},
                        {"role": "user", "content": canonical(req["request"])},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "plugins": [],
                    # Runs since 2026-10-02 record their request options (output cap, reasoning,
                    # routing, usage accounting); earlier runs used the fixed defaults above.
                    **config.get("worker_request_options", {}),
                    "model": config["worker_model"],
                }
                endpoint = "https://openrouter.ai/api/v1/chat/completions"
            audit = path / "provider-responses" / (p["audit_id"] + ".json")
            record = json.loads(audit.read_text())
            assert record["request_hash"] == digest({"endpoint": endpoint, "payload": payload})
            confirmed += 1
            pending = None
    if pending is not None:
        unanswered.append(pending["event_id"])
    if count != confirmed + len(unanswered):
        raise ValueError("worker requests without a retained response or run failure")
    return {
        "run": path.name,
        "worker_requests": count,
        "exact_paid_request_hashes_verified": confirmed,
        "unanswered_requests": unanswered,
        "private_directive_deliveries": private_count,
        "review_flags": flags,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("runs", type=Path, nargs="+")
    args = p.parse_args()
    load_env(ROOT / ".env")
    secrets = [v for k, v in os.environ.items() if k.endswith(("_API_KEY", "_TOKEN")) and len(v) > 12]
    rows = [audit_run(path, secrets) for path in args.runs]
    args.output.write_text(
        json.dumps(
            {
                "runs": rows,
                "worker_requests": sum(r["worker_requests"] for r in rows),
                "note": "Literal payload/credential checks and exact request hashes; not a claim about subjective model awareness.",
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
