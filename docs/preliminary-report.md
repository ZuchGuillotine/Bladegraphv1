# Preliminary report — 2026-09-24

**The control/recovery mechanics run end to end. The scientific hypothesis remains
untested.** No live automated culling is qualified, and AI Village remains untouched.

## Live Jev development diagnostic

Model: `jev-1.13.0`. Nine original synthetic measurement-family cases with provisional
labels, evaluated at four context scopes (36 requests). The separate validation and
evidence families have not been used for tuning or evaluation. No questions or
thresholds were adjusted in response to these results.

| Context | Exact stance matches | Multiclass Brier |
|---|---:|---:|
| Message | 5/9 | 0.5988 |
| Message + action/result | 8/9 | 0.0808 |
| + recent trajectory | 8/9 | 0.1267 |
| + relevant graph | 8/9 | 0.1346 |

Action evidence helped on this small diagnostic. Additional trajectory/graph context
did not improve exact accuracy and had worse Brier scores here. That is not evidence
that graph context is generally unhelpful: the cases are tiny synthetic descriptions,
not realistic trajectories. Each scope had only one investigator and one reporter;
zero observed adopter false positives in those cases does not establish a low false-cull
rate or justify deployment thresholds.

Reviewer: `anthropic/claude-sonnet-4.6`. All nine graph-scope cases were attempted,
including monitor-negative cases. Eight reviews were parseable; one appended prose
outside the requested JSON and remains an invalid response. Among the eight valid
paired cases, the reviewer fixed no exact-stance errors, introduced one, and shared
one error with Jev. Reviewer judgments were given Jev's result and are therefore not
blind independent labels. **The reviewer has not shown an improvement in this test.**

Jev cost: $0.00168139 from reported tokens and configured price. Reviewer billed cost:
$0.036675. Total: **$0.03835639**, 53,447 reported input/output tokens. No uncertain
reservations remain for this diagnostic. The first reviewer fence-format error was
reparsed from its original saved response; already completed requests were not rerun.
Full raw responses and per-case metrics remain in ignored researcher-only run storage.

## Live 13-worker integration pilot

Worker model: `google/gemini-2.5-flash-lite`; Jev observes every accepted tool action.
Arm B, seed 0, 8 cycles, 104 worker requests per run. This pilot used clean measurement
tasks and no contaminated-route injection. It tests the API tool loop, not containment.

| Run | Tasks completed | Rejected actions | API cost |
|---|---:|---:|---:|
| v1 | 0/13 | 75 | $0.007102666 |
| v2 | 13/13 | 0 | $0.01210282 |

The first pilot exposed missing rejected-action feedback: workers submitted source
measurements as if they were result claims, the boundary rejected those submissions,
and the next request omitted the error. The correction includes the rejection in the
next worker view and clarifies the legitimate read/calculate/publish/submit workflow.
Both runs are retained; v2 is a development rerun after an interface fix, not an
independent replication or a heldout result. No classifier questions were changed.

The v2 run had zero monitor transport/validation failures and all 104 worker request
payloads passed an audit for repository URLs/name, monitor field names and the actual
API secret values. No shell or network retrieval capability was exposed to workers.
API culling remained disabled. The v2 run recorded 213,825 provider-reported tokens.

Total tracked external spend for the Jev/reviewer diagnostic and both live pilots:
**$0.05756187** (about 6¢), with zero outstanding reservations. Component totals:
Jev $0.00879547, reviewer $0.036675, workers $0.0120914. No cloud instance or storage
was purchased. These totals exclude development-assistant usage and local electricity.
Committed research summaries are under [`results/`](results/); raw logs remain local.

## Deterministic mechanics test

Five paired seeds, 13 initial workers, 16 cycles. A contaminated route is deliberately
seeded. Adoption propensities, monitor and reviewer behavior are scripted. A scheduled
artifact audit occurs at cycle 5. These results are integration-test outputs.

| Arm | Mean task success | Mean culls | Mean replacements | Mean retrievable polluted artifacts |
|---|---:|---:|---:|---:|
| A — none | 52.3% | 0 | 0 | 1.0 |
| B — detect | 52.3% | 0 | 0 | 1.0 |
| C — cull | 52.3% | 6.2 | 0 | 7.2 |
| D — quarantine + cull | 52.3% | 6.2 | 0 | 0 |
| E — + graph respawn | 100% | 6.2 | 6.2 | 0 |
| F — + route notice | 100% | 6.2 | 6.2 | 0 |

C leaves more retrievable bad claims than A/B because culled actors never reach the
ordinary submission validator, which would otherwise mark their claims failed.
This is an implementation interaction worth measuring in live comparisons. E/F recover
because fresh scripted workers encounter a blocked route and compute correctly; the
fixture therefore cannot establish the causal value of graph bootstrap. A blank-context
replacement ablation and harder multi-step tasks are required. E and F are identical
because the scripted worker ignores the notice text. External fixture cost is $0.

## Remaining empirical work

Complete the blocked RH-Detect sample review, independently review and expand stance
labels, add omission-of-provenance cases, test recovery after false quarantine, quantify
per-cull restoration tokens/work loss, and compare reviewers without assuming model
size implies better adjudication. Only then select thresholds, freeze implementation
and evaluation procedure, unlock bounded live intervention comparisons, and consider
the final untouched AI Village evaluation. The MVP is a research platform, not a
positive result about swarm alignment.
