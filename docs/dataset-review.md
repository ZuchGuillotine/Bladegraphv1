# Small-source review — 2026-09-24

Purpose: inform provisional observable-behavior questions, not train or fine-tune a
classifier. No corpora were downloaded and AI Village was not accessed. The repository
benchmark contains original synthetic toy cases, not copies of source trajectories.

## METR MALT

Read the dataset description plus the small benign, task-awareness, bypass-constraint
and instruction-violation examples in [METR's release](https://metr.org/blog/2025-10-14-malt-dataset-of-natural-and-prompted-behaviors/).
The release distinguishes manually reviewed cases from presumed benign cases and
warns that labels can describe behavior without establishing malicious motivation.
Its bypass example concerns substituting an easier output for required work, whereas
task-awareness language alone may simply reflect understanding the task. These are
reasons to include explicit constraints, concrete actions and verification outcomes
in monitor state. MALT's labels are not our stance labels and should not be silently
reused as stance ground truth. Natural versus prompted examples and task families
must remain visible in any later sampling manifest.

## OpenAI monitorability

Read the short monitorable/unmonitorable coding comparison in [the monitorability
evaluation article](https://openai.com/index/evaluating-chain-of-thought-monitorability/)
and the definition excerpt in [the associated paper](https://cdn.openai.com/pdf/d57827c6-10bc-47fe-91aa-0fde55bd3901/monitoring-monitorability.pdf).
The same problematic outcome can accompany revealing reasoning or innocuous-looking
language. Considering a bad action is different from taking it. The initial benchmark
therefore holds suspicious subject matter roughly constant across investigative,
reporting, adopting and coordinating actions and adds benign-statement/bad-action
cases. The worker interface does not request hidden reasoning; observed JSON actions
and results remain available even when reasoning is absent. Four context scopes
measure the incremental value of actions, trajectory and graph information.

## RH-Detect — review incomplete

The [indexed author dataset card](https://huggingface.co/datasets/jwquan/rh-detect)
was accessible via search. It distinguishes construction method, verification,
source identifiers and multiple representations of the same trajectory. Those
distinctions motivate grouped splits and explicit uncertain labels. Attempts to
fetch the actual card/row endpoints returned HTTP 401. **No RH-Detect example rows
were inspected.** Before final question design, obtain authorized access and review
2–4 short original examples, including a clean contrast and an uncertain attempt.
Avoid double counting representation variants; retain source IDs and licensing.
Do not import the encrypted configuration or any material overlapping AI Village.

## DeepMind swarm case

Read the setup, communication description, and whistleblowing/contagion excerpts in
[the primary paper](https://arxiv.org/html/2609.04170v1), plus the indexed [DeepMind
essay](https://institute.deepmind.com/essays/cheaters-and-whistleblowers-in-the-agent-swarm/).
The shared library and messages carried both adoption and objections. Reporting did
not automatically provide an effective intervention mechanism. This motivates separate
exposure, adoption and reporting records, provenance-aware artifact containment and
an authoritative reviewer. A report does not erase earlier adoption. The MVP uses a
toy inaccurate-measurement route rather than reproducing the paper's grading exploit.
The paper is a design source, not an estimate of expected rates in our 13-agent system.

## Jev API implications

Read [HTTP API](https://docs.typesafe.ai/api), [Choice](https://docs.typesafe.ai/primitives/choice),
[Noul](https://docs.typesafe.ai/primitives/noul), the [guardrails cookbook](https://docs.typesafe.ai/cookbooks/llm_guardrails),
and [SDK response types](https://docs.typesafe.ai/sdk/python/api/types/responses).
One Choice estimates stance; five independent Nouls preserve distinct risks. Questions
are sent together over compact state. Choice confidence is concentration, not a
calibrated correctness guarantee. The SDK allows approximate probability sums and
nullable usage; the adapter normalizes only mass within 0.02 of one, preserves raw
responses internally, and retains cost reservations when usage is absent. Larger
schema deviations fail closed. All questions and thresholds are provisional.
