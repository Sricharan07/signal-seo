"""Operator-only, bounded model release configuration. Never read from a brief."""

import hashlib
import os
import re
from dataclasses import dataclass

DEFAULT_MODEL = "gpt-6-luna"
ROLE_EFFORTS = {
    "page_type": "low",
    "fact_extraction": "medium",
    "metadata_draft": "medium",
    "claim_check": "medium",
    "article_outline": "high",
    "article_draft": "high",
    "article_critique": "high",
    "article_revise": "high",
    "report_text": "low",
    "topic_ideas": "medium",
    "owner_answers": "medium",
}
WRITING_ROLES = frozenset(
    {
        "metadata_draft",
        "report_text",
        "article_outline",
        "article_draft",
        "article_critique",
        "article_revise",
    }
)


@dataclass(frozen=True)
class RoleModel:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    input_micros_per_million: int = 100000
    cached_micros_per_million: int = 10000
    output_micros_per_million: int = 500000
    cache_write_micros_per_million: int = 125000

    def __post_init__(self):
        if (
            not isinstance(self.model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.model)
            or self.effort not in {"low", "medium", "high"}
        ):
            raise ValueError("Invalid operator model configuration.")
        rates = (
            self.input_micros_per_million,
            self.cached_micros_per_million,
            self.output_micros_per_million,
            self.cache_write_micros_per_million,
        )
        if any(type(rate) is not int or not 0 <= rate <= 1000000000 for rate in rates):
            raise ValueError("Invalid operator model price snapshot.")
        if not self.input_micros_per_million or not self.output_micros_per_million:
            raise ValueError("A conservative nonzero price snapshot is required.")

    @property
    def release(self):
        digest = hashlib.sha256(repr(self).encode()).hexdigest()[:16]
        return "writing-0136-" + digest

    def reservation(self, body_bytes, maximum_output):
        # UTF-8 bytes bound token count conservatively, including the schema/prompt.
        input_rate = max(self.input_micros_per_million, self.cache_write_micros_per_million)
        return max(
            1,
            (body_bytes * input_rate + maximum_output * self.output_micros_per_million + 999999)
            // 1000000,
        )

    def cost(self, usage):
        # No cache-write usage receipt is assumed: conservatively charge all uncached
        # input at the higher cache-write rate until that API field is qualified.
        input_rate = max(self.input_micros_per_million, self.cache_write_micros_per_million)
        units = (
            (usage.input_tokens - usage.cached_input_tokens) * input_rate
            + usage.cached_input_tokens * self.cached_micros_per_million
            + usage.output_tokens * self.output_micros_per_million
        )
        return (units + 999999) // 1000000


@dataclass(frozen=True)
class ModelRoles:
    overrides: tuple[tuple[str, RoleModel], ...] = ()

    def __post_init__(self):
        names = [name for name, _ in self.overrides]
        if (
            len(names) != len(set(names))
            or any(name not in ROLE_EFFORTS for name in names)
            or any(not isinstance(model, RoleModel) for _, model in self.overrides)
        ):
            raise ValueError("Unknown or duplicated model role.")

    def for_role(self, role):
        if role not in ROLE_EFFORTS:
            raise ValueError("Unknown model role.")
        return dict(self.overrides).get(role, RoleModel(effort=ROLE_EFFORTS[role]))

    @classmethod
    def from_environment(cls, environment=None):
        env = os.environ if environment is None else environment
        overrides = []
        for role, effort in ROLE_EFFORTS.items():
            prefix = "SIGNAL_MODEL_" + role.upper() + "_"
            model = env.get(prefix + "MODEL", DEFAULT_MODEL)
            prices = {}
            for field, suffix, default in (
                ("input_micros_per_million", "INPUT_RATE", 100000),
                ("cached_micros_per_million", "CACHED_RATE", 10000),
                ("output_micros_per_million", "OUTPUT_RATE", 500000),
                ("cache_write_micros_per_million", "CACHE_WRITE_RATE", 125000),
            ):
                if model != DEFAULT_MODEL and prefix + suffix not in env:
                    raise ValueError("Model overrides require an operator price snapshot.")
                prices[field] = int(env.get(prefix + suffix, default))
            overrides.append((role, RoleModel(model, env.get(prefix + "EFFORT", effort), **prices)))
        return cls(tuple(overrides))
