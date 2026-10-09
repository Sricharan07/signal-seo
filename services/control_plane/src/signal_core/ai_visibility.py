"""Evidence-first AI-search visibility observations over assistant boundaries.

Provider response text is untrusted data.  This module only reads documented
citation structures; it never follows a URL or interprets answer prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.assistant_credentials import AssistantProvider
from signal_core.assistant_providers import (
    AssistantProviderError,
    OpenBaoAssistantCredentials,
    record_assistant_evidence,
    request_assistant_search,
)
from signal_core.database import _clean_transaction
from signal_core.shared_egress import SharedEgressProvider

MAX_QUESTIONS_PER_SET = 25
MAX_OWNER_QUESTIONS = 10
MAX_QUESTION_LENGTH = 512
MAX_CITATIONS = 32
RESERVED_COST_MICROS = {"openai": 25_000, "perplexity": 25_000, "gemini": 25_000}
_PROVIDER_MODELS = {
    "openai": "gpt-4.1-mini",
    "perplexity": "fast",
    "gemini": "gemini-2.5-flash",
}


class AiVisibilityUnavailable(RuntimeError):
    """The exact crawl, provider evidence, or visibility projection is unavailable."""


@dataclass(frozen=True)
class CrawlQuestionSource:
    page_evidence_id: UUID
    url: str
    title: str | None
    headings: tuple[str, ...]


@dataclass(frozen=True)
class TargetQuestion:
    question: str
    source_kind: str
    source_evidence_id: UUID | None


@dataclass(frozen=True)
class CitationParse:
    status: str
    cited_pages: tuple[str, ...]
    other_domains: tuple[str, ...]
    citations: tuple[str, ...]
    failure_code: str | None = None


@dataclass(frozen=True)
class VisibilityObservation:
    id: UUID
    provider: AssistantProvider
    model: str
    observed_at: datetime
    status: str
    provider_evidence_id: UUID | None
    cited_pages: tuple[str, ...]
    other_domains: tuple[str, ...]
    usage: dict[str, int]
    reserved_cost_micros: int
    failure_code: str | None


def derive_target_questions(
    crawl_sources: tuple[CrawlQuestionSource, ...], owner_questions: tuple[str, ...]
) -> tuple[TargetQuestion, ...]:
    """Create a bounded, inspectable question set without model-generated targets."""
    if not crawl_sources:
        raise AiVisibilityUnavailable("Crawl evidence is required.")
    if len(owner_questions) > MAX_OWNER_QUESTIONS:
        raise ValueError("Too many owner questions.")
    questions: list[TargetQuestion] = []
    seen: set[str] = set()

    def add(question: str, source_kind: str, source_id: UUID | None) -> None:
        clean = _question(question)
        key = clean.casefold()
        if key not in seen and len(questions) < MAX_QUESTIONS_PER_SET:
            seen.add(key)
            questions.append(TargetQuestion(clean, source_kind, source_id))

    for source in crawl_sources:
        if not isinstance(source.page_evidence_id, UUID):
            raise ValueError("Invalid crawl evidence identity.")
        subject = source.title or next(iter(source.headings), None)
        if subject:
            add(f"What is {subject}?", "crawl", source.page_evidence_id)
    for question in owner_questions:
        add(question, "owner", None)
    if not questions:
        raise AiVisibilityUnavailable("Crawl evidence yielded no usable target question.")
    return tuple(questions)


def parse_citations(provider: AssistantProvider, response: dict, site_origin: str) -> CitationParse:
    """Extract only provider citation fields and classify malformed structures explicitly."""
    if provider not in _PROVIDER_MODELS or not isinstance(response, dict):
        raise ValueError("Unsupported citation response.")
    site_host = _origin_host(site_origin)
    try:
        values, present = _citation_values(provider, response)
        if len(values) > MAX_CITATIONS:
            return CitationParse("incomplete", (), (), (), "CITATION_LIMIT_EXCEEDED")
        urls = tuple(_citation_url(value) for value in values)
    except (TypeError, ValueError):
        return CitationParse("incomplete", (), (), (), "MALFORMED_CITATIONS")
    if not present:
        return CitationParse("complete", (), (), ())
    cited_pages = tuple(sorted({url for url in urls if _url_host(url) == site_host}))
    other_domains = tuple(sorted({_url_host(url) for url in urls if _url_host(url) != site_host}))
    return CitationParse("complete", cited_pages, other_domains, tuple(sorted(set(urls))))


async def observe_question(
    connection: Connection,
    credentials: OpenBaoAssistantCredentials,
    egress: SharedEgressProvider,
    *,
    tenant_id: UUID,
    site_id: UUID,
    question_id: UUID,
    provider: AssistantProvider,
    question: str,
    site_origin: str,
    remaining_cost_micros: int,
) -> VisibilityObservation:
    """Perform at most one qualified provider request and always project incompleteness."""
    if remaining_cost_micros < RESERVED_COST_MICROS[provider]:
        return _incomplete(question_id, provider, "COST_CAP_REACHED")
    try:
        result = await request_assistant_search(
            credentials, egress, provider=provider, question=question, operation_id=uuid4()
        )
        recorded = record_assistant_evidence(
            connection, tenant_id=tenant_id, site_id=site_id, result=result
        )
        parsed = parse_citations(provider, result.response, site_origin)
        usage = _usage(provider, result.response)
        return VisibilityObservation(
            uuid4(),
            provider,
            result.model_reported,
            datetime.now(UTC),
            parsed.status,
            recorded.evidence_id,
            parsed.cited_pages,
            parsed.other_domains,
            usage,
            RESERVED_COST_MICROS[provider],
            parsed.failure_code,
        )
    except AssistantProviderError as error:
        return _incomplete(question_id, provider, error.code)


def record_target_question_set(
    connection: Connection,
    *,
    tenant_id: UUID,
    site_id: UUID,
    crawl_manifest_id: UUID,
    questions: tuple[TargetQuestion, ...],
    session_token: str | None = None,
    generation: str | None = None,
    request_id: UUID | None = None,
    supersedes_id: UUID | None = None,
) -> UUID:
    if not questions or len(questions) > MAX_QUESTIONS_PER_SET:
        raise ValueError("Invalid target question set.")
    set_id = request_id or uuid4()
    payload = [
        {
            "id": str(uuid5(NAMESPACE_URL, f"ai-question:{set_id}:{index}")),
            "question": item.question,
            "source_kind": item.source_kind,
            "source_evidence_id": str(item.source_evidence_id) if item.source_evidence_id else None,
        }
        for index, item in enumerate(questions)
    ]
    with _clean_transaction(connection):
        if session_token is not None:
            from signal_core.business_brain import _session

            token_hash, generation = _session(session_token, generation)
            outcome = connection.execute(
                "SELECT control.ai_visibility_owner_approve_questions(%s,%s,%s,%s,%s,%s,%s)",
                (
                    token_hash,
                    generation,
                    site_id,
                    set_id,
                    crawl_manifest_id,
                    supersedes_id,
                    Jsonb(payload),
                ),
            ).fetchone()[0]
        else:
            outcome = connection.execute(
                "SELECT control.record_ai_visibility_question_set(%s, %s, %s, %s, %s)",
                (tenant_id, site_id, set_id, crawl_manifest_id, Jsonb(payload)),
            ).fetchone()[0]
    if outcome not in {"recorded", "replayed"}:
        from signal_core.content_writer import ContentWriterRejected, ContentWriterUnavailable

        if outcome == "denied":
            raise ContentWriterUnavailable("owner_access_denied")
        if session_token is not None:
            raise ContentWriterRejected(outcome)
        raise AiVisibilityUnavailable("Target question set could not be recorded.")
    return set_id


def load_crawl_question_sources(
    connection: Connection,
    *,
    tenant_id: UUID,
    site_id: UUID,
    crawl_manifest_id: UUID,
    session_token: str | None = None,
    generation: str | None = None,
) -> tuple[CrawlQuestionSource, ...]:
    """Load only immutable page metadata from one completed crawl manifest."""
    with _clean_transaction(connection):
        if session_token is not None:
            from signal_core.business_brain import _session

            token_hash, generation = _session(session_token, generation)
            rows = connection.execute(
                "SELECT * FROM control.ai_visibility_owner_sources(%s,%s,%s,%s)",
                (token_hash, generation, site_id, crawl_manifest_id),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM control.load_ai_visibility_crawl_sources(%s, %s, %s)",
                (tenant_id, site_id, crawl_manifest_id),
            ).fetchall()
    if not rows or len(rows) > 1000:
        raise AiVisibilityUnavailable("Crawl evidence is unavailable.")
    sources = []
    for page_id, url, title, headings in rows:
        if (
            not isinstance(page_id, UUID)
            or not isinstance(url, str)
            or not isinstance(headings, list)
        ):
            raise AiVisibilityUnavailable("Crawl evidence is invalid.")
        labels = []
        for heading in headings:
            if not isinstance(heading, dict) or not isinstance(heading.get("text"), str):
                raise AiVisibilityUnavailable("Crawl headings are invalid.")
            labels.append(heading["text"])
        sources.append(CrawlQuestionSource(page_id, url, title, tuple(labels)))
    return tuple(sources)


def record_visibility_observation(
    connection: Connection,
    *,
    tenant_id: UUID,
    site_id: UUID,
    question_id: UUID,
    observation: VisibilityObservation,
) -> None:
    with _clean_transaction(connection):
        outcome = connection.execute(
            "SELECT control.record_ai_visibility_observation("
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                site_id,
                observation.id,
                question_id,
                observation.provider,
                observation.model,
                observation.status,
                observation.provider_evidence_id,
                Jsonb(list(observation.cited_pages)),
                Jsonb(list(observation.other_domains)),
                Jsonb(
                    {
                        "usage": observation.usage,
                        "reserved_cost_micros": observation.reserved_cost_micros,
                        "failure_code": observation.failure_code,
                    }
                ),
            ),
        ).fetchone()[0]
    if outcome != "recorded":
        raise AiVisibilityUnavailable("Visibility observation could not be recorded.")


def _incomplete(question_id: UUID, provider: AssistantProvider, code: str) -> VisibilityObservation:
    del question_id  # The caller binds the immutable question record.
    return VisibilityObservation(
        uuid4(),
        provider,
        _PROVIDER_MODELS[provider],
        datetime.now(UTC),
        "incomplete",
        None,
        (),
        (),
        {},
        0,
        code,
    )


def _question(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Question must be text.")
    clean = " ".join(value.split())
    if not 8 <= len(clean) <= MAX_QUESTION_LENGTH:
        raise ValueError("Question is outside the allowed bounds.")
    return clean


def _citation_values(provider: str, response: dict) -> tuple[list[object], bool]:
    if provider == "openai":
        output = response.get("output")
        if not isinstance(output, list):
            raise TypeError
        values: list[object] = []
        present = False
        for item in output:
            if not isinstance(item, dict):
                raise TypeError
            if item.get("type") != "message":
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                raise TypeError
            for part in content:
                if not isinstance(part, dict):
                    raise TypeError
                annotations = part.get("annotations", [])
                if not isinstance(annotations, list):
                    raise TypeError
                for annotation in annotations:
                    if not isinstance(annotation, dict):
                        raise TypeError
                    if annotation.get("type") == "url_citation":
                        present = True
                        values.append(annotation.get("url"))
        return values, present
    if provider == "perplexity":
        citations = response.get("citations")
        if citations is None:
            return [], False
        if not isinstance(citations, list):
            raise TypeError
        return list(citations), True
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        raise TypeError
    values = []
    present = False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TypeError
        metadata = candidate.get("groundingMetadata")
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            raise TypeError
        chunks = metadata.get("groundingChunks", [])
        if not isinstance(chunks, list):
            raise TypeError
        for chunk in chunks:
            if not isinstance(chunk, dict) or not isinstance(chunk.get("web"), dict):
                raise TypeError
            present = True
            values.append(chunk["web"].get("uri"))
    return values, present


def _citation_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _origin_host(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("Invalid site origin.")
    return parsed.hostname.lower().rstrip(".")


def _url_host(value: str) -> str:
    host = urlsplit(value).hostname
    if host is None:
        raise ValueError
    return host.lower().rstrip(".")


def _usage(provider: str, response: dict) -> dict[str, int]:
    raw = response.get("usageMetadata") if provider == "gemini" else response.get("usage")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        if isinstance(key, str) and type(value) is int and 0 <= value <= 10_000_000:
            result[key] = value
    return result
