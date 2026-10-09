"""Validated non-secret API process configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiSettings:
    environment: str = "development"
    expose_docs: bool = False

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("SIGNAL_ENVIRONMENT must be development, test, or production.")
        if self.environment == "production" and self.expose_docs:
            raise ValueError("Interactive API documentation is disabled in production.")

    @classmethod
    def from_environment(cls) -> "ApiSettings":
        raw_docs = os.environ.get("SIGNAL_EXPOSE_API_DOCS", "false").lower()
        if raw_docs not in {"true", "false"}:
            raise ValueError("SIGNAL_EXPOSE_API_DOCS must be true or false.")
        return cls(
            environment=os.environ.get("SIGNAL_ENVIRONMENT", "development"),
            expose_docs=raw_docs == "true",
        )
