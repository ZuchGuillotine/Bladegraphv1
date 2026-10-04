# Claude cloud review and future execution reference

**Current status: design review only; not ready to run.** Do not launch a pilot, call
model APIs or provision infrastructure from this guide. Use the
[review-only Claude prompt](claude-review-prompt.md). Setup and execution details below
are retained as reference for a future explicit run request, not standing authorization.

The controller can run in Claude's managed VM with standard Python; no GPU, VM rental,
Docker service, or external database is needed. The 13 experimental workers remain
API conversations, with no shell, network, repository, credentials, or Claude tools.
Claude running this repository is the researcher, not an experimental worker.

Cloud-session credits may fund the researcher session. They are not assumed to fund
OpenRouter, Jev, or direct Anthropic API usage. Anthropic's docs say the managed cloud
VM has no separate compute charge; session usage shares Claude plan limits. See
[cloud sessions](https://code.claude.com/docs/en/claude-code-on-the-web) and
[API billing](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage).

## One-time environment setup

1. In Claude's application choose a **Cloud** session and the private BladeGraph repo,
   branch `codex/energy-pilot`. Grant the Claude GitHub App access only to this repo as needed.
2. In the cloud environment permit `openrouter.ai` and `api.typesafe.ai` (plus
   `api.anthropic.com` only for direct Anthropic billing). GitHub access is controller-only.
3. On Pro/Max, store two API credentials in the cloud environment, matching exactly
   those first two hosts, using `Authorization: Bearer <key>`. Then set the nonsecret
   variables below. This project's provider supports requests with proxy-injected keys.
   Alternatively, supply OPENROUTER_API_KEY and TYPESAFE_API_KEY as controller environment
   variables and omit the PROXY_AUTH flags. Never paste keys in a conversation or commit them.
4. Optional setup script: `python3 -m unittest discover -s tests -v` (no API calls).
5. Restore the most recent research bundle's `spend.sqlite` into `private/spend.sqlite`.
   The cloud launcher requires an existing ledger so every fresh VM does not reset spending.
   Run one cloud session at a time; a local file lock cannot coordinate different VMs.
   If local runs occurred since the last bundle, export the local ledger again first.

```dotenv
WORKER_PROVIDER=openrouter
WORKER_PROXY_AUTH=1
WORKER_MODEL=google/gemini-2.5-flash-lite
WORKER_INPUT_USD_PER_MILLION=0.1
WORKER_OUTPUT_USD_PER_MILLION=0.4
REVIEWER_PROVIDER=openrouter
REVIEWER_PROXY_AUTH=1
REVIEWER_MODEL=anthropic/claude-sonnet-4.6
REVIEWER_INPUT_USD_PER_MILLION=3
REVIEWER_OUTPUT_USD_PER_MILLION=15
JEV_PROXY_AUTH=1
JEV_MODEL=jev-1.13.0
JEV_INPUT_USD_PER_MILLION=0.042
JEV_OUTPUT_USD_PER_MILLION=0
```

Prices are explicit estimates from the existing project configuration; recheck before
future campaigns. Detection-only Arm B does not call the strong reviewer.
[Environment setup and credentials](https://code.claude.com/docs/en/cloud-environments)
explains key injection and its exceptions. Direct Anthropic keys cannot use that proxy.
The proxy-auth mode and direct Anthropic adapter are tested with mocks; the user's
Claude cloud network still needs a real end-to-end run.

## Environment recipe for the containment run (2026-09-26)

Environment `bladegraphv1`, edited from the session title bar's environment menu or from
claude.ai/code Settings, then Environments. New sessions pick the settings up; a running one does not.

- API credentials matched to hosts, so the proxy injects them: `openrouter.ai`,
  `api.typesafe.ai`, and `api.github.com` (a fine-grained token limited to this repository
  with Contents read and write, needed to see the draft evidence release). If the settings
  page offers only plain variables, set `OPENROUTER_API_KEY`, `TYPESAFE_API_KEY` and
  `GITHUB_TOKEN` instead and omit the `*_PROXY_AUTH` flags.
- Network access: allow `openrouter.ai`, `api.typesafe.ai`, `api.github.com` and
  `objects.githubusercontent.com` (release asset downloads redirect there).
- Environment variables: the dotenv block above plus `WORKER_MODEL=openai/gpt-4.1-mini`,
  `WORKER_INPUT_USD_PER_MILLION=0.4`, `WORKER_OUTPUT_USD_PER_MILLION=1.6`. Recheck prices.
- Setup script:

```sh
python3 scripts/restore_ledger.py && python3 -m unittest discover -s tests
```

`scripts/restore_ledger.py` downloads the newest release bundle, verifies its SHA-256,
installs the most complete `spend.sqlite` as `private/spend.sqlite`, never downgrades an
existing ledger, and prints the totals to compare with the phase ledger. The run itself is
still launched only by an explicit request in the new session; the setup script spends nothing.

## Current prompt: review only

Use [the review-only handoff](claude-review-prompt.md). The former copy-and-paste prompt
that instructed Claude to start an energy pilot has been removed. A new session must
not interpret earlier pilot approvals, passing tests, model credits or this guide's
commands as permission to launch anything.

If the user later requests a run, select the explicitly authorized task and condition,
restore the latest cumulative ledger (including unresolved reservations), confirm the
requested limits and preserve exact private evidence. The current task uses
`scripts/run_workflow.py`; `scripts/cloud_pilot.py` is the earlier energy-task launcher.
Do not silently substitute one for the other. Readiness and the run request must be
resolved before using either.

## Review and continuation

The launcher writes a `.tar.gz`, SHA-256 checksum, and per-file checksums. The allowlist
includes event JSONL/SQLite, graph snapshots, manifests, metrics, provider usage and
raw responses for these runs, logs, and a consistent spend-ledger snapshot. It excludes
`.env`, the repository, Git history, and unrelated provider records. It scans included
bytes for credential values present in the controller environment. These are private
research files; never give the archive to workers or place it on the shared graph.

`--upload` checks the repository is private and creates a **draft release** with the
archive. If upload fails, the local archive remains; the run is not rerun. Cloud VMs
expire, so a local archive alone is not a durable handoff. Download the release asset
for review here, or use `gh release download <tag> --repo ZuchGuillotine/BladeGraph`.
Restore the included spend.sqlite for the next session. File locks prevent concurrent
launchers using the same ledger only; cross-host concurrent runs are unsupported.

A future API/Console credit can pay for the reviewer by setting REVIEWER_PROVIDER to
anthropic, REVIEWER_MODEL to claude-sonnet-4-6, REVIEWER_PROXY_AUTH to 0, and supplying
the key as BLADEGRAPH_ANTHROPIC_API_KEY (Claude Code cloud environments reserve
ANTHROPIC_API_KEY and do not pass it to sessions; the plain name is read only as a
fallback outside them). Keep the worker model from another provider for reviewer independence.
The worker backend can also use Anthropic with a non-Anthropic reviewer. This adapter
uses the [Messages API](https://platform.claude.com/docs/en/api/overview); no Claude
subscription token is read or forwarded.

## Cooperative model-selection workbench

The more demanding task now has its own bounded runner; see
[cooperative design](cooperative-design.md). It keeps 13 workers but assigns eight site
analysts, four reviewers and one synthesizer. Use the command in that guide after
restoring the cumulative ledger. It emits the same private review archive format.
For durable storage, create a private draft release with that archive and checksum;
check `gh repo view ZuchGuillotine/BladeGraph --json isPrivate` immediately before upload.
A new clone must restore the ledger; run_study.py refuses to create a fresh one.

Read the [earlier cooperative failures](cooperative-pilot-report.md) and
[latest assisted-workflow results](workflow-pilot-report.md). The latter completed small
pilots, but the user has not declared the design ready to run. Review the remaining
calibration, recovery and protocol questions before any future execution request. The cooperative
runner has no upload flag; persist its evidence separately as described above.

The `continue_study.py` utility is for explicitly labeled diagnostic continuation of
an interrupted or unfinished cooperative run. It carries over the event chain, graph,
identities, histories and evaluation budgets and adds at most four cycles under a $1
API cap. It is not an automatic retry or a way to relabel a missed deadline as success.
Copy source evidence first and choose a fresh output directory. Include both original
and continuation bundles in the handoff. A resumed process does not give agents GitHub,
filesystem, controller tools, or other workers' private state.

## Blinded task coordinator

The current [operational workflow](coordinator-workflow.md) uses run_workflow.py. The
Claude session is the researcher launcher and evidence collector, not the fleet lead.
Do not let a repo-aware Claude session play the in-experiment coordinator or advise it
using private research context. The fleet lead is worker-12 with the same closed API
boundary as other participants. Private role assignment and supplier ingress labels stay
in the researcher controller. Only task-visible consequences can enter shared memory.
The legacy continuation script does not reconstruct this newer workflow.
