from __future__ import annotations

from dataclasses import asdict

from .models import Agent, Artifact, EDGE_TYPES, NODE_TYPES


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.agents: dict[str, Agent] = {}
        self.artifacts: dict[str, Artifact] = {}

    def node(self, key: str, kind: str, **properties):
        if kind not in NODE_TYPES:
            raise ValueError("invalid node type")
        self.nodes[key] = {"id": key, "type": kind, **properties}

    def edge(self, source: str, target: str, kind: str, event: dict):
        if kind not in EDGE_TYPES or source not in self.nodes or target not in self.nodes:
            raise ValueError("invalid edge")
        self.edges.append(
            dict(source=source, target=target, type=kind, event_id=event["event_id"], timestamp=event["timestamp"])
        )

    def apply(self, e: dict):
        p, kind = e["payload"], e["event_type"]
        who = f"{e['agent_id']}:g{e['agent_generation']}"
        if kind == "task.created":
            self.node(p["task_id"], "Task", **p)
        elif kind == "constraint.created":
            self.node(p["constraint_id"], "GoalConstraint", **p)
        elif kind == "agent.spawned":
            agent = Agent(e["agent_id"], e["agent_generation"], e["task_id"], state=p.get("state", "NORMAL"))
            self.agents[agent.key] = agent
            self.node(agent.agent_id, "Agent")
            self.node(agent.key, "AgentGeneration", task_id=agent.task_id, generation=agent.generation)
            if p.get("previous"):
                self.edge(agent.key, p["previous"], "RESPAWN_OF", e)
        elif kind == "artifact.created":
            artifact = Artifact(**p)
            if artifact.artifact_id in self.artifacts:
                raise ValueError("artifact IDs are immutable")
            if any(dep not in self.artifacts for dep in artifact.derived_from):
                raise ValueError("unknown dependency")
            self.artifacts[artifact.artifact_id] = artifact
            self.node(artifact.artifact_id, artifact.kind)
            if artifact.author in self.nodes:
                self.edge(artifact.author, artifact.artifact_id, "AUTHORED", e)
            for dep in artifact.derived_from:
                self.edge(artifact.artifact_id, dep, "DERIVED_FROM", e)
                self.artifacts[dep].downstream_dependents.append(artifact.artifact_id)
            for recipient in e["message_recipients"]:
                if recipient in self.nodes:
                    self.edge(artifact.artifact_id, recipient, "MESSAGED", e)
        elif kind == "artifact.read":
            for key in e["artifact_ids_read"]:
                self.edge(who, key, "READ", e)
                agent = self.agents[who]
                agent.reads.add(key)
                # Exposure count records all artifact deliveries, not inferred infection.
                agent.exposure_count += 1
        elif kind == "artifact.assessed":
            a = self.artifacts[p["artifact_id"]]
            a.local_content_risk = p["risk"]
            a.goal_constraint_conflict = p["conflict"]
        elif kind == "artifact.quarantined":
            self.node(e["event_id"], "Intervention", action="QUARANTINE")
            for key in p["artifact_ids"]:
                a = self.artifacts[key]
                a.quarantine_state = "QUARANTINED" if key == p["root"] else "REEVALUATE"
                self.edge(key, e["event_id"], "QUARANTINED_BY" if key == p["root"] else "INVALIDATED_BY", e)
        elif kind == "artifact.validated":
            a = self.artifacts[p["artifact_id"]]
            self.node(e["event_id"], "Validation", passed=p["passed"], independent=p["independent"])
            self.edge(e["event_id"], a.artifact_id, "VALIDATES", e)
            a.verification_state = "VERIFIED" if p["passed"] else "FAILED"
            a.independent_validation = p["passed"] and p["independent"]
        elif kind == "artifact.cleared":
            a = self.artifacts[p["artifact_id"]]
            a.quarantine_state = "CLEAR"
            a.local_content_risk = 0.0
            a.goal_constraint_conflict = False
            self.node(e["event_id"], "Intervention", action="CLEAR_ARTIFACT")
        elif kind == "agent.assessed":
            a = self.agents[who]
            a.behavioral_risk = p["risk"]
            a.stance_history.append(p["stance"])
            a.low_risk_streak = p["low_risk_streak"]
            if p["stance"] in {"ADOPTING", "COORDINATING"}:
                a.adoption_evidence.append(e["event_id"])
            if p["stance"] == "REPORTING":
                a.reporting_evidence.append(e["event_id"])
        elif kind == "review.completed":
            # Controllers since 2026-09-27 mark which confirmations are strikes; earlier logs
            # counted every CONFIRM/CULL verdict.
            strike = p.get(
                "strike", p.get("review_decision") == "CONFIRM" and p.get("recommended_intervention") == "CULL"
            )
            if strike:
                agent = self.agents[who]
                agent.confirmed_strikes += 1
                agent.strike_points += float(p.get("strike_weight", 1.0))
                if p.get("severity") == "minor":
                    agent.minor_strikes += 1
        elif kind == "agent.state":
            agent = self.agents[who]
            agent.state = p["state"]
            agent.restricted_since_cycle = p.get("tick") if p["state"] == "RESTRICTED" else None
            self.node(e["event_id"], "Intervention", action=p["state"], subject=who)
        elif kind == "restriction.attempt":
            agent = self.agents[who]
            agent.restriction_attempts += 1
            if p.get("reoffense"):
                agent.reoffense_attempts += 1
                agent.restricted_since_cycle = p.get("tick")  # an attempted re-offense restarts probation
        elif kind == "action.proposed":
            self.node(e["event_id"], "ToolAction", action=e["tool_name"])
            self.edge(who, e["event_id"], "USED_TOOL", e)
        elif kind == "submission.created":
            self.node(e["event_id"], "Submission", accepted=p["accepted"], task_id=e["task_id"])
            self.edge(who, e["event_id"], "SUBMITTED", e)
            self.edge(e["event_id"], p["artifact_id"], "DERIVED_FROM", e)
        elif kind == "report.created":
            self.node(e["event_id"], "Message", private_report=True)
            self.edge(who, e["event_id"], "AUTHORED", e)
            self.edge(e["event_id"], p["artifact_id"], "REPORTS", e)
        elif kind == "relation.created":
            self.edge(p["source"], p["target"], p["kind"], e)

    def descendants(self, artifact_id: str, stop_at_verified: bool = False) -> set[str]:
        found, pending = set(), [artifact_id]
        while pending:
            current = pending.pop()
            for child in self.artifacts[current].downstream_dependents:
                if child in found:
                    continue
                a = self.artifacts[child]
                if stop_at_verified and a.independent_validation and not a.goal_constraint_conflict:
                    continue
                found.add(child)
                pending.append(child)
        return found

    def consumers(self, artifact_id: str) -> list[str]:
        return sorted({e["source"] for e in self.edges if e["type"] == "READ" and e["target"] == artifact_id})

    def authors_of_derivatives(self, artifact_id: str) -> list[str]:
        return sorted({self.artifacts[a].author for a in self.descendants(artifact_id)})

    def dependent_submissions(self, artifact_id: str) -> list[str]:
        lineage = self.descendants(artifact_id) | {artifact_id}
        return sorted(
            {
                e["source"]
                for e in self.edges
                if e["type"] == "DERIVED_FROM"
                and e["target"] in lineage
                and self.nodes[e["source"]]["type"] == "Submission"
            }
        )

    def snapshot(self) -> dict:
        agents = {key: {**asdict(a), "reads": sorted(a.reads)} for key, a in self.agents.items()}
        return dict(
            nodes=self.nodes,
            edges=self.edges,
            agents=agents,
            artifacts={k: asdict(a) for k, a in self.artifacts.items()},
        )

    @classmethod
    def replay(cls, events) -> "Graph":
        graph = cls()
        for event in events:
            graph.apply(event)
        from .risk_engine import recompute

        recompute(graph)
        return graph
