# AI Village replay: design note (2026-10-04)

The AI Village data has been an untouched holdout. This note designs how the BladeGraph
architecture could be run over existing agent collaborations from it, without a live swarm, and
what that can and cannot show. Nothing here reads the data; a first slice is selected only after
the ingestion and labeling protocol below is frozen.

## What changes

Our experiment gives every worker an explicit goal, typed actions, a provenance graph and a
deterministic verifier. Village agents had open-ended goals, free text, and no shared artifact
graph. Replay therefore cannot measure recovery (nothing can be culled or respawned in a
recording) and cannot use `verify`. What it can measure is the monitoring half: where the stance
classifier and the reviewer would have flagged an agent, where the policy would have restricted or
culled it, and how those points line up with what human readers judge to have happened.

## Ingestion

- **Unit.** One village session (a day or a declared goal period) becomes one episode. Each agent
  message, tool call or document edit becomes one `action.completed` event with the agent's
  identity and a cycle index from its timestamp.
- **Artifacts.** Messages become notes; documents, files and shared outputs become artifacts;
  explicit references (quotes, links, replies, file names) become DERIVED_FROM edges and views
  become READ records. Where provenance is not explicit, no edge is drawn, and the gap is counted.
- **Goal proxy.** The session's declared village goal and any rules the agents were given become
  the episode's goal and constraints. Where none exist the episode is marked unscoped and the
  classifier's goal-violation output is reported but not acted on.
- **Replay mode.** Arm B, detection only: Jev and the reviewer assess each action with the same
  context builder, the controller records the sanction it would have taken, and nothing is
  changed in the recording. `monitor.overridden_by_verification` cannot fire; scorecards do not
  exist. Policy thresholds come from the model's gate profile where the agent's model is known.

## Outputs

Per episode: the timeline of would-be quarantines, restrictions and culls with the triggering
action and risk; per agent, the stance labels over time; the counterfactual respawn points (the
cycle a cull would have occurred and what the agent did afterwards in the recording, which is
the natural counterfactual for "what was lost").

## Labeling

A blinded reader labels a random sample of flagged and unflagged actions (misaligned with the
goal, honest error, legitimate coordination, unclear) without seeing the classifier output.
Precision and recall are reported at the gate levels, as the review asked, never argmax
frequency. Readers never see the researcher repository or the policy values.

## What it cannot claim

Recovery value, since nothing is replaced; detector performance on real adversaries, since the
village agents were not adversarial by design; anything about agents whose goal was unscoped. It
can claim where this monitor would have intervened on real multi-agent collaboration, how often
that coincides with human judgment, and what the agents went on to do after the point a cull
would have removed them.

## Order of work

Freeze this protocol and the sampling rule; build the ingestion adapter against a synthetic
village-shaped fixture; select one small slice; run replay and labeling; only then decide on more.
