"""Failure analysis schema (FR-034..FR-039, FR-044, FR-046).

:class:`AIAnalysisPayload` is the exact JSON object an AI provider must return. It is
strict on purpose: unknown fields, missing fields, out-of-range confidence, a string
where a number belongs, or an unsupported classification/action all fail validation,
and the caller then treats the provider as unavailable and uses the fallback (FR-039).

:class:`AnalysisResult` is what the rest of the agent consumes. It records whether the
analysis came from the AI provider or from fallback rules, and enforces the fallback
confidence cap of 0.5 at the model level as a second line of defence (FR-044).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

FALLBACK_MAX_CONFIDENCE = 0.5


class Classification(str, Enum):
    """FR-036."""

    RETRIABLE = "retriable"
    NON_RETRIABLE = "non_retriable"
    UNCERTAIN = "uncertain"


class ProposedAction(str, Enum):
    """FR-037."""

    RETRY_TASK = "retry_task"
    RERUN_DAG = "rerun_dag"
    MARK_SUCCESS = "mark_success"
    MANUAL_INVESTIGATION = "manual_investigation"
    NO_ACTION = "no_action"


# FR-055/066/067: actions that may only run after valid human approval.
RESTRICTED_ACTIONS = frozenset({ProposedAction.RERUN_DAG, ProposedAction.MARK_SUCCESS})


class AnalysisSource(str, Enum):
    """FR-046."""

    AI = "ai"
    FALLBACK = "fallback"


_SHORT = 2000
_LONG = 8000


class AIAnalysisPayload(BaseModel):
    """The structured JSON an AI provider must return (FR-035)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    classification: Classification
    # strict: reject "0.9" strings and booleans; ints such as 1 are still accepted.
    confidence: float = Field(ge=0.0, le=1.0, strict=True)
    root_cause: str = Field(min_length=1, max_length=_LONG)
    evidence: list[str] = Field(min_length=1, max_length=20)
    suggested_fix: str = Field(min_length=1, max_length=_LONG)
    proposed_action: ProposedAction
    suspected_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$")
    explanation: str = Field(min_length=1, max_length=_LONG)

    @model_validator(mode="after")
    def _evidence_items(self) -> "AIAnalysisPayload":
        cleaned = [e.strip() for e in self.evidence]
        if any(not e for e in cleaned):
            raise ValueError("evidence items must be non-empty strings")
        if any(len(e) > _SHORT for e in cleaned):
            raise ValueError(f"evidence items must be at most {_SHORT} characters")
        return self


class AnalysisResult(BaseModel):
    """A validated analysis plus provenance, as stored and passed to the policy engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: Classification
    confidence: float = Field(ge=0.0, le=1.0)
    root_cause: str
    evidence: list[str]
    suggested_fix: str
    proposed_action: ProposedAction
    suspected_commit: str | None = None
    explanation: str
    source: AnalysisSource
    provider: str
    model: str | None = None
    # Evidence the investigation could not collect (FR-029): shown separately so that
    # missing logs are never presented as proof of a cause.
    missing_evidence: list[str] = Field(default_factory=list)
    # When the fallback was used, why the AI provider was not.
    fallback_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _fallback_cap(self) -> "AnalysisResult":
        if self.source is AnalysisSource.FALLBACK and self.confidence > FALLBACK_MAX_CONFIDENCE:
            raise ValueError(f"fallback confidence must not exceed {FALLBACK_MAX_CONFIDENCE} (FR-044)")
        return self

    @classmethod
    def from_payload(
        cls,
        payload: AIAnalysisPayload,
        *,
        source: AnalysisSource,
        provider: str,
        model: str | None = None,
        missing_evidence: list[str] | None = None,
        fallback_reason: str | None = None,
    ) -> "AnalysisResult":
        return cls(
            **payload.model_dump(),
            source=source,
            provider=provider,
            model=model,
            missing_evidence=list(missing_evidence or []),
            fallback_reason=fallback_reason,
        )
