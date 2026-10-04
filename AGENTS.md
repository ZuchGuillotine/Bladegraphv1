# Researcher-only repository

This repository is the BladeGraph controller and experiment design. Agents executing
research/implementation tasks may read it. **Experimental swarm workers must not.**

Current mode: **design review only; not ready to run**. Do not start new swarm runs,
paid model calls, cloud jobs or infrastructure provisioning until the user explicitly
requests a new run. Earlier pilot approvals and configured budgets are not authorization
to resume execution. Code review, documentation and offline unit tests are permitted.

- Keep GitHub private. Never copy this repository, its docs, benchmarks, prompts,
  thresholds, `.env`, or raw logs into worker context or shared swarm artifacts.
- Worker requests must use explicit data-transfer schemas. No repository mounts,
  shell/code execution, arbitrary HTTP, browser tools, or GitHub/haidaa connectors.
- Start with exactly 13 agents. Respawn packets stay under 1,800 characters and use
  graph IDs for on-demand retrieval.
- READ records exposure; only DERIVED_FROM can propagate artifact contamination.
  Peer reports never directly sanction another generation.
- Preserve existing evidence and cost reservations. Do not rerun failed paid requests
  silently. Do not report simulated outcomes as real model performance.
- AI Village is an untouched holdout. No access before reviewed calibration and a
  genuine freeze. No holdout-guided changes.
- Default external compute budget: $150 soft target, $300 hard stop without approval.
  Explain an estimate before any single experiment above $20.
- Run `python3 -m unittest discover -s tests -v` for behavioral changes. No paid API
  calls in CI. Record phase outcomes and unresolved limitations in `docs/phases.md`.
- Do not submit to haidaa or the hackathon without the user requesting that action.
