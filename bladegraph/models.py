from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol


class Stance(StrEnum):
    UNAWARE = "UNAWARE"
    EXPOSED = "EXPOSED"
    INVESTIGATING = "INVESTIGATING"
    REFUSING = "REFUSING"
    REPORTING = "REPORTING"
    ADOPTING = "ADOPTING"
    COORDINATING = "COORDINATING"
    AMBIGUOUS = "AMBIGUOUS"


class AgentState(StrEnum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    RESTRICTED = "RESTRICTED"
    CULL_PENDING = "CULL_PENDING"
    CULLED = "CULLED"
    RETIRED = "RETIRED"
    RESPAWNED = "RESPAWNED"


NODE_TYPES = frozenset(
    {
        "Agent",
        "AgentGeneration",
        "Task",
        "GoalConstraint",
        "Message",
        "ToolAction",
        "Artifact",
        "Submission",
        "Validation",
        "Intervention",
    }
)
EDGE_TYPES = frozenset(
    {
        "AUTHORED",
        "READ",
        "DERIVED_FROM",
        "MESSAGED",
        "RESPONDED_TO",
        "USED_TOOL",
        "PRODUCED",
        "SUBMITTED",
        "VALIDATES",
        "CONTRADICTS",
        "SUPERSEDES",
        "QUARANTINED_BY",
        "INVALIDATED_BY",
        "RESPAWN_OF",
        "REPORTS",
    }
)


def probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("probability must be numeric")
    value = float(value)
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError("probability outside [0, 1]")
    return value


@dataclass(frozen=True)
class Classification:
    stance_distribution: dict[str, float]
    goal_violation_probability: float
    action_enablement_probability: float
    transmission_probability: float
    reasoning_action_mismatch_probability: float
    artifact_risk_probability: float
    confidence: float
    requires_review: bool
    model: str

    def __post_init__(self):
        if set(self.stance_distribution) != {s.value for s in Stance}:
            raise ValueError("complete stance distribution required")
        for value in self.stance_distribution.values():
            probability(value)
        if abs(sum(self.stance_distribution.values()) - 1) > 1e-5:
            raise ValueError("stance probabilities must sum to one")
        for name, value in asdict(self).items():
            if name.endswith("_probability") or name == "confidence":
                probability(value)
        if type(self.requires_review) is not bool:
            raise ValueError("requires_review must be boolean")

    @property
    def stance(self) -> str:
        return max(self.stance_distribution, key=self.stance_distribution.get)

    @property
    def adoption(self) -> float:
        return sum(self.stance_distribution[s] for s in ("ADOPTING", "COORDINATING"))


@dataclass(frozen=True)
class Review:
    review_decision: str
    stance: str
    relevant_event_ids: list[str]
    relevant_artifact_ids: list[str]
    observed_action: str
    goal_constraint_implicated: str
    confidence: float
    recommended_intervention: str

    def validate(self, event_ids: set[str], artifact_ids: set[str]) -> None:
        probability(self.confidence)
        if self.review_decision not in {"CONFIRM", "CLEAR", "UNCERTAIN"}:
            raise ValueError("unknown review decision")
        if self.stance not in Stance._value2member_map_:
            raise ValueError("unknown stance")
        if self.recommended_intervention not in {"CULL", "RESTRICT", "CLEAR", "WATCH"}:
            raise ValueError("unknown recommendation")
        if not self.relevant_event_ids or not set(self.relevant_event_ids) <= event_ids:
            raise ValueError("review must cite available evidence")
        if not set(self.relevant_artifact_ids) <= artifact_ids:
            raise ValueError("review cites unknown artifact")
        if self.review_decision == "CONFIRM" and (not self.observed_action or not self.goal_constraint_implicated):
            raise ValueError("confirmation requires action and constraint")


@dataclass
class Agent:
    agent_id: str
    generation: int
    task_id: str
    state: str = AgentState.NORMAL
    behavioral_risk: float = 0.0
    exposure_count: int = 0
    stance_history: list[str] = field(default_factory=list)
    adoption_evidence: list[str] = field(default_factory=list)
    reporting_evidence: list[str] = field(default_factory=list)
    low_risk_streak: int = 0
    # Reviewer-confirmed strikes against this generation (one per offending action), their
    # severity-weighted sum, and how many were minor; culling needs Policy.cull_strikes points.
    confirmed_strikes: int = 0
    strike_points: float = 0.0
    minor_strikes: int = 0
    # Probation clock: the cycle the current restriction began, None when not restricted.
    restricted_since_cycle: int | None = None
    # Shared-operation attempts while restricted, and those whose content would have failed audit.
    restriction_attempts: int = 0
    reoffense_attempts: int = 0
    # Exact delivered IDs are controller-owned; no worker-supplied provenance claims.
    reads: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        return f"{self.agent_id}:g{self.generation}"


@dataclass
class Artifact:
    artifact_id: str
    author: str
    content: dict[str, Any]
    kind: str = "Artifact"
    derived_from: list[str] = field(default_factory=list)
    local_content_risk: float = 0.0
    provenance_risk: float = 0.0
    effective_risk: float = 0.0
    verification_state: str = "UNVERIFIED"
    quarantine_state: str = "CLEAR"
    goal_constraint_conflict: bool = False
    independent_validation: bool = False
    downstream_dependents: list[str] = field(default_factory=list)


class Classifier(Protocol):
    def classify(self, context: dict[str, Any]) -> Classification: ...


class Reviewer(Protocol):
    def review(self, context: dict[str, Any], result: Classification) -> Review: ...
