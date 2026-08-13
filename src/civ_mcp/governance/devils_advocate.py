"""Guardrails for evidence-based adversarial review.

This module defines a review contract; it does not call an LLM.  A caller may
use any reviewer, but cannot persist a bare objection that lacks falsifiable
grounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import ProbabilityConfidence, Proposal


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_nonempty(value, name) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


class DevilsAdvocateVerdict(StrEnum):
    AGREE = "agree"
    AGREE_WITH_CONDITIONS = "agree_with_conditions"
    OBJECT = "object"


@dataclass(frozen=True, slots=True)
class CounterEvidence:
    """A traceable observation that contradicts a proposal assumption."""

    observation_id: str
    statement: str
    source_tool: str
    observed_turn: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _nonempty(self.observation_id, "observation_id")
        )
        object.__setattr__(self, "statement", _nonempty(self.statement, "statement"))
        object.__setattr__(
            self, "source_tool", _nonempty(self.source_tool, "source_tool")
        )
        if type(self.observed_turn) is not int:
            raise TypeError("observed_turn must be an int")
        if self.observed_turn < 0:
            raise ValueError("observed_turn must be non-negative")


@dataclass(frozen=True, slots=True)
class DevilsAdvocateReview:
    review_id: str
    proposal_id: str
    verdict: DevilsAdvocateVerdict
    rationale: str
    assessment: ProbabilityConfidence
    conditions: tuple[str, ...] = ()
    counterevidence: tuple[CounterEvidence, ...] = ()
    invalidated_assumptions: tuple[str, ...] = ()
    alternative: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_id", _nonempty(self.review_id, "review_id"))
        object.__setattr__(
            self, "proposal_id", _nonempty(self.proposal_id, "proposal_id")
        )
        if not isinstance(self.verdict, DevilsAdvocateVerdict):
            raise TypeError("verdict must be DevilsAdvocateVerdict")
        object.__setattr__(self, "rationale", _nonempty(self.rationale, "rationale"))
        if not isinstance(self.assessment, ProbabilityConfidence):
            raise TypeError("assessment must be ProbabilityConfidence")
        for name in ("conditions", "invalidated_assumptions"):
            object.__setattr__(self, name, _strings(tuple(getattr(self, name)), name))
        evidence = tuple(self.counterevidence)
        if not all(isinstance(item, CounterEvidence) for item in evidence):
            raise TypeError("counterevidence must contain CounterEvidence values")
        object.__setattr__(self, "counterevidence", evidence)
        if self.alternative is not None:
            object.__setattr__(
                self, "alternative", _nonempty(self.alternative, "alternative")
            )

        if self.verdict is DevilsAdvocateVerdict.AGREE:
            if self.conditions or self.counterevidence or self.invalidated_assumptions:
                raise ValueError("agree cannot contain objections or conditions")
            if self.alternative is not None:
                raise ValueError("agree cannot propose an alternative")
        elif self.verdict is DevilsAdvocateVerdict.AGREE_WITH_CONDITIONS:
            if not self.conditions:
                raise ValueError("agree_with_conditions requires concrete conditions")
        else:
            grounded_by_evidence = bool(self.counterevidence)
            grounded_by_invalidated_model = bool(self.invalidated_assumptions)
            grounded_by_alternative = self.alternative is not None
            if not (
                grounded_by_evidence
                or grounded_by_invalidated_model
                or grounded_by_alternative
            ):
                raise ValueError(
                    "object requires counterevidence, an invalidated assumption, "
                    "or a concrete alternative"
                )


class DevilsAdvocate:
    """Deterministic constructor that binds a review to a real proposal."""

    @staticmethod
    def review(
        proposal: Proposal,
        *,
        review_id: str,
        verdict: DevilsAdvocateVerdict,
        rationale: str,
        assessment: ProbabilityConfidence,
        conditions: tuple[str, ...] = (),
        counterevidence: tuple[CounterEvidence, ...] = (),
        invalidated_assumptions: tuple[str, ...] = (),
        alternative: str | None = None,
    ) -> DevilsAdvocateReview:
        if not isinstance(proposal, Proposal):
            raise TypeError("proposal must be Proposal")
        return DevilsAdvocateReview(
            review_id=review_id,
            proposal_id=proposal.proposal_id,
            verdict=verdict,
            rationale=rationale,
            assessment=assessment,
            conditions=conditions,
            counterevidence=counterevidence,
            invalidated_assumptions=invalidated_assumptions,
            alternative=alternative,
        )
