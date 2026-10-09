"""Bounded crawl/document extraction with intent-before-I/O and atomic proposed facts."""

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from html.parser import HTMLParser
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from signal_core.brand_documents import _SECRET, read_brand_document
from signal_core.business_brain import (
    BusinessBrainRejected,
    BusinessBrainUnavailable,
    FactProvenance,
    _session,
    page_type_question,
    read_brain,
    validate_candidate_facts,
)
from signal_core.crawl_artifacts import (
    ArtifactEncryptionKey,
    ArtifactRecord,
    EncryptedLocalArtifactStore,
)
from signal_core.database import Scope, _clean_transaction
from signal_core.decision_contracts import canonical_json
from signal_core.decision_records import PostgresDecisionRecorder
from signal_core.jev_decisions import DecisionService, JevHttpAdapter
from signal_core.model_budget import PostgresModelBudget
from signal_core.model_reasoning import BusinessBrainModelAdapter, MetadataDraftError

EXTRACTION_VERSION = "business-brain-v1"


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


@dataclass(frozen=True)
class ExtractionState:
    state: str
    extraction_id: UUID | None = None
    decision_id: UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BusinessBrainExtractor:
    store: EncryptedLocalArtifactStore
    key: ArtifactEncryptionKey
    model: BusinessBrainModelAdapter | None
    primary: JevHttpAdapter
    decision_connection_factory: Callable[[], Connection]

    @property
    def configured(self) -> bool:
        return isinstance(self.model, BusinessBrainModelAdapter) and self.model.configured

    async def extract(
        self,
        connection: Connection,
        *,
        session_token: str,
        current_recovery_generation: str,
        site_id: UUID,
        provenance: FactProvenance,
    ) -> ExtractionState:
        read_brain(
            connection,
            session_token=session_token,
            current_recovery_generation=current_recovery_generation,
            site_id=site_id,
        )
        if not self.configured:
            return ExtractionState("unavailable", reason="MODEL_UNCONFIGURED")
        if provenance.source_kind == "owner_statement":
            raise BusinessBrainRejected("invalid_extraction_source")
        session_hash, generation = _session(session_token, current_recovery_generation)
        start, end = 0, 0
        if provenance.source_kind == "brand_document":
            source = read_brand_document(
                connection,
                self.store,
                self.key,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
                document_id=provenance.source_id,
            )
            start, end = provenance.extracted_range["start"], provenance.extracted_range["end"]
            if end > len(source.text) or end - start > 24000:
                raise BusinessBrainRejected("invalid_extracted_range")
            text, title, url = (
                source.text[start:end],
                "Owner brand document",
                "document:" + str(source.document_id),
            )
            tenant_id = source.tenant_id
        else:
            with _clean_transaction(connection):
                packet = connection.execute(
                    "SELECT control.business_brain_page_source(%s,%s,%s,%s)",
                    (session_hash, generation, site_id, provenance.source_id),
                ).fetchone()[0]
            if packet is None:
                raise BusinessBrainUnavailable("source_unavailable")
            artifact = packet["artifact"]
            record = ArtifactRecord(
                UUID(artifact["tenant_id"]),
                UUID(artifact["site_id"]),
                UUID(artifact["id"]),
                artifact["object_key"],
                artifact["object_version"],
                artifact["sha256"][2:],
                artifact["byte_length"],
                artifact["media_type"],
                artifact["encryption_key_ref"],
                datetime.fromisoformat(artifact["created_at"]),
                datetime.fromisoformat(artifact["retain_until"]),
                artifact["legal_hold"],
                artifact["durability_state"],
            )
            body = self.store.read(record, key=self.key)
            if hashlib.sha256(body).hexdigest() != packet["body_sha256"]:
                raise BusinessBrainUnavailable("source_integrity_failed")
            parser = _VisibleText()
            parser.feed(body.decode("utf-8", errors="replace"))
            text = " ".join(parser.parts)[:24000]
            title, url, tenant_id = packet["title"], packet["url"], UUID(packet["tenant_id"])
        if not text.strip() or _SECRET.search(text):
            raise BusinessBrainUnavailable("source_withheld")
        request = page_type_question(page_url=url, title=title, text=text)
        with _clean_transaction(connection):
            row = connection.execute(
                "SELECT * FROM control.business_brain_begin_extraction("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    session_hash,
                    generation,
                    site_id,
                    uuid4(),
                    provenance.source_kind,
                    provenance.source_id,
                    start,
                    end,
                    EXTRACTION_VERSION,
                    hashlib.sha256(text.encode()).digest(),
                    request.decision_id,
                    uuid4(),
                ),
            ).fetchone()
        extraction_id, decision_id, model_id, state, _ = row
        if state != "started":
            return ExtractionState(state, extraction_id, decision_id)
        request = replace(request, decision_id=decision_id)
        model = replace(
            self.model, budget=PostgresModelBudget(connection, session_token, generation, site_id)
        )
        try:
            with self.decision_connection_factory() as decision_connection:
                recommendation = await DecisionService(
                    self.primary,
                    PostgresDecisionRecorder(decision_connection, Scope(tenant_id, site_id)),
                    model,
                ).recommend(request)
            choice = recommendation.answers.get("page_type", {}).get("choice")
            if choice not in {"product", "pricing", "blog", "docs", "legal", "other"}:
                raise MetadataDraftError("CLASSIFICATION_UNAVAILABLE", retryable=False)
            # Current owner authority is checked again after classification and before the reasoner.
            read_brain(
                connection,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
            result = await model.extract_facts(text=text, page_type=choice, operation_id=model_id)
            validate_candidate_facts(result.output["facts"])
            with _clean_transaction(connection):
                outcome = connection.execute(
                    "SELECT control.business_brain_finish_extraction("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        session_hash,
                        generation,
                        site_id,
                        extraction_id,
                        choice,
                        canonical_json(result.output["facts"]),
                        hashlib.sha256(canonical_json(result.output["facts"])).digest(),
                        result.provider_response_id,
                        result.model_reported,
                        Jsonb(asdict(result.usage)),
                        None,
                    ),
                ).fetchone()[0]
        except (MetadataDraftError, BusinessBrainRejected) as error:
            with _clean_transaction(connection):
                connection.execute(
                    "SELECT control.business_brain_finish_extraction("
                    "%s,%s,%s,%s,NULL,NULL,NULL,NULL,NULL,NULL,%s)",
                    (
                        session_hash,
                        generation,
                        site_id,
                        extraction_id,
                        getattr(error, "code", "MODEL_OUTPUT_INVALID"),
                    ),
                )
            return ExtractionState("failed", extraction_id, decision_id, "EXTRACTION_FAILED")
        if outcome not in {"completed", "replayed"}:
            raise BusinessBrainUnavailable("extraction_unavailable")
        return ExtractionState("completed", extraction_id, decision_id)
