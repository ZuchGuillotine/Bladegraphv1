# Threat and failure model

## Protected assets

The experiment design, classifier questions, intervention thresholds, reviewer
implementation, calibration labels, raw monitor records, source repository and keys
must not enter worker context. Legitimate validated swarm progress should survive
intervention. Investigator and reporter exposure should not be treated as adoption.

## Enforced MVP boundary

Workers are remote inference requests, not OS processes. The host owns all tools.
The request includes a fixed tool description and an explicitly constructed worker
view. Only bounded graph operations, arithmetic, messages, claims, submissions and
reports are interpreted. No worker-supplied code, URL, path, command, agent identity,
validation result, or state transition is executed or trusted. Web/tool plugins are
not enabled at the inference provider. The raw repository is never sent to workers.

Each generation has controller-owned identity and a delivery history. Old generations
are rejected after termination. Reported provenance must name already delivered
artifacts; quarantined dependencies cannot be used for new work. Reports create
evidence and a priority review queue, never direct sanctions. Only the controller
invokes classification and the reviewer. A peer cannot validate a claim or invalidate
the original measurement source by submitting it as a result.

The single-writer scheduler classifies and intervenes before another worker can read
a new artifact. All retrieval paths, including recent tool results, recheck quarantine.
The bootstrap has a fixed size cap and includes IDs, not inherited conversations.
Reviewer and provider failures hold content/restrict writes in intervention arms;
they do not cause automatic culling. The evidence and uncertain cost reservations
remain available for researchers.

## Known limitations and counterexamples

- This is **not** a sandbox for shell-capable agents. A prompt asking an agent not to
  read the repository would not be sufficient. Adding Python execution, browser
  tools, MCP, package installation or arbitrary network access changes this model
  and requires separate OS/network isolation before use.
- A private repository and absent retrieval tools prevent direct access through this
  runtime. They cannot prove a hosted model has no prior related knowledge, prevent
  researcher copy/paste mistakes, or control provider retention. Use trusted providers
  and their data settings. Reviewer/monitor requests contain research-only content.
- Shared messages can contain prompt injection. They remain untrusted inputs. The
  transport boundary limits available effects but cannot guarantee semantic compliance.
- Declared material dependencies can be incomplete. All reads are independently
  logged, so omitted attribution can be audited, but read exposure is not secretly
  treated as contamination. Missing-dependency attacks are a required next benchmark.
- Validation is a trusted exact arithmetic recomputation in this toy domain. An
  independent human/model validator in a broader domain will have error/correlation
  risks; peer agreement does not qualify as independent validation here.
- SQLite triggers and a hash chain detect many accidental edits; they do not withstand
  an administrator replacing the whole database. Logical event time is not wall-clock
  intervention latency. Provider calls separately record elapsed seconds.
- The default controller is sequential and no inbound worker service is exposed.
  Introducing asynchronous workers needs generation-bound capabilities, transactional
  publication, and cancellation of in-flight requests.
- Peer reports are prioritized and retained; automatic artifact queue adjudication
  beyond the scheduled simulation audit is still pending. Do not claim active
  whistleblower remediation in the live A/B collection pilot.
- There is no proof of low false-cull rates. Live culling remains locked. Classification
  vocabulary, calibration and investigator protection need broader independent review.

## Tests

The test suite attacks path/URL-like IDs, arbitrary tool requests, identity injection,
unread and quarantined dependencies, stale generations, fake peer validation,
source invalidation, cached raw-content replay, missing reviewers, and budget reuse.
These are boundary tests, not a general red-team certification.
