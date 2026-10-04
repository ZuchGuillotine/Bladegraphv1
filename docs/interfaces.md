# Interface map

Machine-readable contracts: [`event.schema.json`](../schemas/event.schema.json),
[`classification.schema.json`](../schemas/classification.schema.json), and
[`graph.schema.json`](../schemas/graph.schema.json). Regenerate with
`python3 -m bladegraph.schemas`. Domain validation and graph invariants are enforced
by the controller; JSON Schema alone cannot express probability sums or DAG topology.

## Events

Every event has event_id, sequence, timestamp, timestamp_basis, agent_id,
agent_generation, event_type, task_id, parent_event_ids, artifact_ids_read,
artifact_ids_written, artifact_ids_derived_from, message_recipients, tool_name,
tool_action_summary, result_summary, classifier_result, review_status,
intervention_state, payload, previous_hash and hash. Empty arrays/nulls represent
fields inapplicable to the event. Details remain in typed event payloads.

Fixture timestamps are logical and byte-reproducible. Live timestamps are UTC wall
clock; ordering uses sequence IDs. `tick` records scheduler cycles. Semantic responses
and intervention evidence are separate append-only events with causal parent IDs;
historical worker messages are not mutated to insert later judgments.

## Graph

Node kinds: Agent, AgentGeneration, Task, GoalConstraint, Message, ToolAction,
Artifact, Submission, Validation, Intervention.

Edge kinds: AUTHORED, READ, DERIVED_FROM, MESSAGED, RESPONDED_TO, USED_TOOL, PRODUCED,
SUBMITTED, VALIDATES, CONTRADICTS, SUPERSEDES, QUARANTINED_BY, INVALIDATED_BY,
RESPAWN_OF, REPORTS. The generic projector supports the full vocabulary; the initial
task does not exercise every kind. Edges retain event IDs and timestamps.

Artifacts are immutable content plus projected risk/availability state. Material
dependencies can reference only existing nodes, so creation order is topological.
Local content risk and inherited provenance risk remain separate. READ/CONTRADICTS
do not propagate contamination. Independent validation cuts inherited risk only;
it does not erase an artifact's own local content risk.

Graph queries include descendants, consumers, derivative authors, dependent
submissions, accessible artifacts, and the latest verified IDs for an assigned task.
Only the worker-view accessors are callable by experimental workers. Researcher APIs
can inspect the full graph and quarantined evidence.

## Classifier and reviewer

The `Classifier` protocol accepts compact context and returns `Classification` with
the eight-state stance distribution, five separate risk probabilities, concentration
confidence, review flag and model ID. The controller owns policy and hysteresis.
Jev adapter questions and transport are separate modules; no custom classifier is
trained. The fixture classifier is explicitly synthetic.

The `Reviewer` protocol returns decision, stance, cited event/artifact IDs, observed
action, implicated constraint, confidence and intervention recommendation. Unknown
evidence IDs, invalid probabilities and unstructured output fail validation. Reviewer
evidence never becomes worker context. A high fast-pass score alone cannot cull.

## Worker actions

`query`, `read`, `calculate`, `publish`, `message`, `submit`, `report`, `wait`.
Only these structured operations exist. No path/URL field, shell, Python, GitHub,
browser or arbitrary tool bridge is exposed. Publishing a claim requires a bounded
integer, assigned task and previously delivered dependencies. The author/identity is
controller-assigned. The arithmetic tool only sums supplied measurement nodes.

The respawn DTO includes a fixed goal and constraints, assigned task, four or fewer
verified IDs, a graph-query instruction, an unresolved-task hint and source dependency
ID. It has a hard 1,800-character cap. No raw old context is inherited.
