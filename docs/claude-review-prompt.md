# Claude handoff: design review only

Use the private BladeGraph repository, branch `codex/energy-pilot` (draft PR #1).
This handoff authorizes review and offline checks only. It does not authorize experimental
execution, cloud provisioning or spending model API credits.

Paste this into a researcher Claude session:

> Review the current BladeGraph design. We are not ready to run experiments yet.
>
> Read AGENTS.md and CLAUDE.md, then docs/workflow-pilot-report.md,
> docs/coordinator-workflow.md and experiments/operational-workflow.json. Inspect the
> corresponding implementation in bladegraph/operational.py, bladegraph/task_workflow.py,
> bladegraph/ingress.py and scripts/run_workflow.py. Use docs/claude-cloud.md only as
> future execution reference. Earlier reports are historical evidence, not current
> launch instructions.
>
> Assess the task-only coordinator boundary, persistence and finalization, separation
> of private-role intent from observed behavior, and the clean/faulty supplier pair.
> Identify remaining gaps in Jev/reviewer calibration, graph contamination handling,
> recovery comparisons and protocol freezing. Distinguish scripted mechanics from live
> results and task-level verification from behavioral sanctions. Do not infer that all
> incorrect claims are malicious or that prompted insiders followed their directive.
>
> Keep the initial population at 13 and recovery packets minimal, with graph retrieval
> on demand. The repo-aware session must never play or privately advise the in-task
> coordinator. Do not give the repository, design, condition labels, private directives
> or research logs to participants or Claude Code subagents acting as participants.
>
> You may inspect code, review existing researcher evidence and run offline unit tests
> (`python3 -m unittest discover -s tests -v`). Do not launch any swarm, invoke model APIs,
> provision infrastructure, configure launch automation, retry paid failures or access
> the AI Village holdout. Preserve the cumulative ledger and its two unresolved Jev
> reservations. Do not merge the draft PR or submit to haidaa or the hackathon.
>
> Return a concise review of material findings and proposed next design changes, with
> file references and remaining decisions. Do not start a run as a follow-up action;
> wait for a future explicit request to execute.

The review status is documented in AGENTS.md, CLAUDE.md and the design configuration.
It is an operating instruction, not a newly implemented CLI lock. Existing run commands
remain in the repository for review and later explicitly authorized work.
