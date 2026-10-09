"""Typed, bounded decision questions and recommendation-only results."""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

import rfc8785

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
MAX_SHARED_INPUT_BYTES = 64 * 1024
MAX_SHARED_INPUT_TOKENS = 64 * 1024
MAX_QUESTION_BYTES = 32 * 1024
MAX_QUESTIONS = 64

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PURPOSE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")


class Recommendation(StrEnum):
    REJECT = "reject"
    ASK_OWNER = "ask_owner"
    SHIP = "ship"

    @property
    def safety_rank(self) -> int:
        return {
            Recommendation.REJECT: 0,
            Recommendation.ASK_OWNER: 1,
            Recommendation.SHIP: 2,
        }[self]

    def reduce_to(self, ceiling: "Recommendation") -> "Recommendation":
        """Keep or reduce a recommendation; never widen the caller's boundary."""
        return self if self.safety_rank <= ceiling.safety_rank else ceiling


def _json_copy(value: object, *, nullable: bool = False) -> JSONValue:
    if value is None and nullable:
        return None
    if not isinstance(value, (str, list, dict)):
        raise ValueError("Decision input must be text, a JSON object, or a JSON array.")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Decision input must contain only finite JSON values.") from None
    if "\x00" in encoded:
        raise ValueError("Decision input cannot contain NUL characters.")
    return copied


def canonical_json(value: object) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError):
        raise ValueError("Decision input must have a canonical JSON representation.") from None


def _option_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 128
        or "\x00" in value
    ):
        raise ValueError("Choice option names must be bounded non-empty strings.")
    return value


@dataclass(frozen=True)
class ChoiceQuestion:
    instructions: JSONValue
    criteria: Mapping[str, JSONValue | None]
    type: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        instructions = _json_copy(self.instructions)
        if not isinstance(self.criteria, Mapping):
            raise ValueError("Choice criteria must be a mapping.")
        if not 2 <= len(self.criteria) <= MAX_CHOICE_OPTIONS:
            raise ValueError("Choice questions require 2 to 255 options.")
        normalized: dict[str, JSONValue | None] = {}
        for raw_name, raw_description in self.criteria.items():
            name = _option_name(raw_name)
            if name in normalized:
                raise ValueError("Choice option names must be unique.")
            normalized[name] = _json_copy(raw_description, nullable=True)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "criteria", MappingProxyType(normalized))
        _bounded_question(self.to_json())

    def to_json(self) -> dict[str, object]:
        return {
            "type": self.type,
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class ScoreQuestion:
    instructions: JSONValue
    criteria: tuple[JSONValue, ...] | list[JSONValue]
    type: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        instructions = _json_copy(self.instructions)
        if not isinstance(self.criteria, (tuple, list)):
            raise ValueError("Score criteria must be an ordered sequence.")
        if not MIN_SCORE_LEVELS <= len(self.criteria) <= MAX_SCORE_LEVELS:
            raise ValueError("Score questions require 2 to 10 levels.")
        normalized = tuple(_json_copy(level) for level in self.criteria)
        object.__setattr__(self, "instructions", instructions)
        object.__setattr__(self, "criteria", normalized)
        _bounded_question(self.to_json())

    def to_json(self) -> dict[str, object]:
        return {
            "type": self.type,
            "instructions": self.instructions,
            "criteria": list(self.criteria),
        }


@dataclass(frozen=True)
class NoulQuestion:
    instructions: JSONValue
    true_criterion: JSONValue | None = None
    false_criterion: JSONValue | None = None
    type: str = field(default="noul", init=False)

    def __post_init__(self) -> None:
        if (self.true_criterion is None) != (self.false_criterion is None):
            raise ValueError("Noul criteria must provide both true and false descriptions.")
        object.__setattr__(self, "instructions", _json_copy(self.instructions))
        object.__setattr__(
            self,
            "true_criterion",
            _json_copy(self.true_criterion, nullable=True),
        )
        object.__setattr__(
            self,
            "false_criterion",
            _json_copy(self.false_criterion, nullable=True),
        )
        _bounded_question(self.to_json())

    def to_json(self) -> dict[str, object]:
        question: dict[str, object] = {"type": self.type, "instructions": self.instructions}
        if self.true_criterion is not None or self.false_criterion is not None:
            question["criteria"] = {
                "true": self.true_criterion,
                "false": self.false_criterion,
            }
        return question


type Question = ChoiceQuestion | ScoreQuestion | NoulQuestion


def _bounded_question(question: Mapping[str, object]) -> None:
    if len(canonical_json(dict(question))) > MAX_QUESTION_BYTES:
        raise ValueError("A decision question exceeds the 32 KiB conservative input bound.")


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: UUID
    purpose: str
    state: JSONValue
    questions: Mapping[str, Question]
    threshold: float
    policy_ceiling: Recommendation
    model: str = field(default="jev-latest", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, UUID) or self.decision_id.version != 4:
            raise ValueError("Decision records require a caller-generated UUIDv4.")
        if not isinstance(self.purpose, str) or _PURPOSE.fullmatch(self.purpose) is None:
            raise ValueError("Decision purpose must be a bounded stable identifier.")
        state = _json_copy(self.state)
        if not isinstance(self.questions, Mapping) or not 1 <= len(self.questions) <= MAX_QUESTIONS:
            raise ValueError("Decision requests require 1 to 64 typed questions.")
        normalized: dict[str, Question] = {}
        for question_id, question in self.questions.items():
            if not isinstance(question_id, str) or _IDENTIFIER.fullmatch(question_id) is None:
                raise ValueError("Question ids must be bounded lowercase identifiers.")
            if not isinstance(question, (ChoiceQuestion, ScoreQuestion, NoulQuestion)):
                raise ValueError("Every decision question must be typed.")
            normalized[question_id] = question
        recommendation = normalized.get("recommendation")
        if not isinstance(recommendation, ChoiceQuestion) or set(recommendation.criteria) != {
            item.value for item in Recommendation
        }:
            raise ValueError(
                "Recommendation decisions require exact ship, ask_owner, and reject options."
            )
        if (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, (int, float))
            or not math.isfinite(float(self.threshold))
            or not 0 <= float(self.threshold) <= 1
        ):
            raise ValueError("Decision confidence threshold must be between zero and one.")
        if not isinstance(self.policy_ceiling, Recommendation):
            raise ValueError("A deterministic recommendation ceiling is required.")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "questions", MappingProxyType(normalized))
        object.__setattr__(self, "threshold", float(self.threshold))
        if len(self.canonical_request) > MAX_SHARED_INPUT_BYTES:
            raise ValueError("Decision request exceeds the 64 KiB conservative input bound.")

    @property
    def question_payload(self) -> dict[str, object]:
        return {question_id: question.to_json() for question_id, question in self.questions.items()}

    @property
    def payload(self) -> dict[str, object]:
        return {"state": self.state, "model": self.model, "questions": self.question_payload}

    @property
    def canonical_request(self) -> bytes:
        return canonical_json(self.payload)

    @property
    def input_sha256(self) -> bytes:
        return hashlib.sha256(canonical_json(self.state)).digest()

    @property
    def question_schema_sha256(self) -> bytes:
        return hashlib.sha256(canonical_json(self.question_payload)).digest()


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    type: str = field(default="choice", init=False)

    def to_json(self) -> dict[str, object]:
        return {
            "type": self.type,
            "choice": self.choice,
            "probabilities": dict(self.probabilities),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    legend: Mapping[str, JSONValue]
    probabilities: Mapping[str, float]
    confidence: float
    type: str = field(default="score", init=False)

    def to_json(self) -> dict[str, object]:
        return {
            "type": self.type,
            "score": self.score,
            "legend": dict(self.legend),
            "probabilities": dict(self.probabilities),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class NoulAnswer:
    noul: float
    type: str = field(default="noul", init=False)

    def to_json(self) -> dict[str, object]:
        return {"type": self.type, "noul": self.noul}


type Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer


@dataclass(frozen=True)
class DecisionEvaluation:
    model_reported: str
    answers: Mapping[str, Answer]
    input_tokens: int
    output_tokens: int

    @property
    def answer_json(self) -> dict[str, object]:
        return {question_id: answer.to_json() for question_id, answer in self.answers.items()}


@dataclass(frozen=True)
class DecisionRecommendation:
    decision_id: UUID
    recommendation: Recommendation
    policy_ceiling: Recommendation
    provider: str
    model_requested: str
    model_reported: str
    answers: Mapping[str, object]
    probabilities: Mapping[str, float]
    confidence: float
    threshold: float
    fallback: bool
    fallback_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.recommendation, Recommendation) or not isinstance(
            self.policy_ceiling, Recommendation
        ):
            raise ValueError("Decision recommendations require closed recommendation values.")
        if self.recommendation.safety_rank > self.policy_ceiling.safety_rank:
            raise ValueError("A decision recommendation cannot widen deterministic policy.")
        if not is_normalized_number(self.confidence) or not is_normalized_number(self.threshold):
            raise ValueError("Decision confidence values must be normalized.")
        if self.fallback != (self.fallback_reason is not None):
            raise ValueError("Fallback decisions require an explicit reason label.")
        if self.fallback_reason is not None and (
            not isinstance(self.fallback_reason, str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.fallback_reason) is None
        ):
            raise ValueError("Fallback reason labels must be bounded stable codes.")
        if self.provider == "typesafe":
            if self.fallback:
                raise ValueError("Primary Jev recommendations cannot claim fallback evidence.")
        elif self.provider in {"openai_fallback", "deterministic_fallback"}:
            if not self.fallback:
                raise ValueError("Fallback providers require explicit fallback evidence.")
        else:
            raise ValueError("Decision recommendation provider is not supported.")
        if not isinstance(self.answers, Mapping):
            raise ValueError("Decision answers must be a JSON object.")
        answers = _json_copy(dict(self.answers))
        if not isinstance(answers, dict):
            raise ValueError("Decision answers must be a JSON object.")
        probabilities = dict(self.probabilities)
        if self.provider == "typesafe":
            if set(probabilities) != {item.value for item in Recommendation}:
                raise ValueError("Jev recommendations require all recommendation probabilities.")
            if not all(is_normalized_number(value) for value in probabilities.values()):
                raise ValueError("Jev probabilities must be normalized numbers.")
            if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-6):
                raise ValueError("Jev probabilities must sum to one.")
        elif probabilities:
            raise ValueError("Fallback classifications cannot invent a probability distribution.")
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "threshold", float(self.threshold))
        object.__setattr__(self, "answers", MappingProxyType(answers))
        object.__setattr__(self, "probabilities", MappingProxyType(probabilities))


def is_normalized_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and 0 <= float(value) <= 1
    )
