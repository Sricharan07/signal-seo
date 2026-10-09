"""Owner-scoped drafting and sandbox-only candidate composition; no publishing port."""

import hashlib
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from signal_core.article_pipeline import draft_with_quality, model_receipt, operation
from signal_core.brand_documents import _SECRET
from signal_core.business_brain import _session, read_brain
from signal_core.business_brain_extraction import _VisibleText
from signal_core.candidate_build_service import build_candidate_from_github
from signal_core.content_writer import (
    ContentWriterRejected,
    ContentWriterUnavailable,
    article_sentences,
    claim_question,
    entailment_question,
    exact,
    grounding_report,
    originality_report,
    render_article,
    validate_article,
    validate_brief,
    validate_claims,
)
from signal_core.crawl_artifacts import ArtifactRecord
from signal_core.database import Scope, _clean_transaction
from signal_core.decision_contracts import Recommendation, canonical_json
from signal_core.decision_records import PostgresDecisionRecorder
from signal_core.github_app import checkout_github_repository
from signal_core.github_pr_extension import (
    inspect_current_github_pr_extension,
    read_github_pr_extension,
)
from signal_core.github_read_binding import read_github_read_binding
from signal_core.jev_decisions import (
    DecisionService,
    FallbackClassification,
    FallbackUnavailable,
    JevHttpAdapter,
)
from signal_core.model_budget import PostgresModelBudget
from signal_core.model_reasoning import ContentWriterModelAdapter, MetadataDraftError
from signal_core.writing_style import voice_packet


def writer_call(connection, session_token, generation, site_id, name, *args):
    if name not in {
        "read",
        "inventory",
        "create_brief",
        "accept_brief",
        "set_cap",
        "begin_draft",
        "finish_draft",
        "seal",
        "review",
        "approve_delivery",
        "voice_rank",
    }:
        raise ValueError("Invalid writer port.")
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        result = connection.execute(
            f"SELECT control.content_writer_{name}(" + ",".join(["%s"] * (3 + len(args))) + ")",
            (token_hash, generation, site_id, *args),
        ).fetchone()[0]
    if (
        result is None
        or result == "denied"
        or isinstance(result, dict)
        and result.get("state") == "denied"
    ):
        raise ContentWriterUnavailable("owner_access_denied")
    return result


def create_brief(
    connection, *, session_token, generation, site_id, payload, proposal=False, supersedes_id=None
):
    validate_brief(payload)
    read = writer_call(connection, session_token, generation, site_id, "inventory")
    if proposal:
        source = next((r for r in read if r["source_id"] == payload["source_ids"][0]), None)
        if source is None or not source["title"].strip():
            raise ContentWriterUnavailable("PROPOSAL_SOURCE_UNAVAILABLE")
        payload = {
            **payload,
            "topic": source["title"][:200],
            "query": source["title"][:200],
            "intent": "informational",
        }
    brief_id = uuid4()
    result = writer_call(
        connection,
        session_token,
        generation,
        site_id,
        "create_brief",
        brief_id,
        Jsonb(payload),
        "evidence_proposal" if proposal else "owner",
        supersedes_id,
    )
    if result != "created":
        raise ContentWriterRejected(result)
    return {"state": "created", "brief_id": str(brief_id)}


def read_crawl_text(connection, store, key, *, session_token, generation, site_id, source_id):
    token_hash, generation = _session(session_token, generation)
    with _clean_transaction(connection):
        packet = connection.execute(
            "SELECT control.business_brain_page_source(%s,%s,%s,%s)",
            (token_hash, generation, site_id, UUID(source_id)),
        ).fetchone()[0]
    if not packet:
        raise ContentWriterUnavailable("SOURCE_UNAVAILABLE")
    a = packet["artifact"]
    record = ArtifactRecord(
        UUID(a["tenant_id"]),
        UUID(a["site_id"]),
        UUID(a["id"]),
        a["object_key"],
        a["object_version"],
        a["sha256"][2:],
        a["byte_length"],
        a["media_type"],
        a["encryption_key_ref"],
        datetime.fromisoformat(a["created_at"]),
        datetime.fromisoformat(a["retain_until"]),
        a["legal_hold"],
        a["durability_state"],
    )
    body = store.read(record, key=key)
    if hashlib.sha256(body).hexdigest() != packet["body_sha256"]:
        raise ContentWriterUnavailable("SOURCE_INTEGRITY_FAILED")
    parser = _VisibleText()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = " ".join(parser.parts)
    if not text.strip() or len(text) > 24000 or _SECRET.search(text):
        raise ContentWriterUnavailable("ORIGINALITY_SOURCE_UNAVAILABLE")
    return {"source_id": source_id, "url": packet["url"], "text": text}


@dataclass
class _EditorialFallback:
    model: ContentWriterModelAdapter
    receipts: list = field(default_factory=list)

    async def classify(self, request, *, primary_failure):
        try:
            result = await self.model.review_claims(request)
            exact(result.output, ("claim_absent",))
            if type(result.output["claim_absent"]) is not bool:
                raise ContentWriterRejected("INVALID_CLAIM_REVIEW")
        except (MetadataDraftError, ContentWriterRejected):
            raise FallbackUnavailable("CLAIM_REVIEW_UNAVAILABLE") from None
        self.receipts.append(
            {
                **model_receipt(result, "claim_check"),
                "response_id": result.provider_response_id,
                "model": result.model_reported,
                "claim_absent": result.output["claim_absent"],
            }
        )
        return FallbackClassification(
            Recommendation.ASK_OWNER,
            0.0,
            result.model_reported,
            "CLAIM_REVIEW_REQUIRED",
            model_requested=result.model_requested,
        )


async def _entailment(primary, recorder, fallback, request):
    before = len(fallback.receipts)
    decision = await DecisionService(primary, recorder, fallback).recommend(request)
    noul = decision.answers.get("claim_absent", {}).get("noul")
    status = "uncertain"
    if not decision.fallback and isinstance(noul, (int, float)):
        status = "supported" if noul <= 0.05 else "unsupported" if noul >= 0.95 else "uncertain"
    elif len(fallback.receipts) > before:
        status = "unsupported" if fallback.receipts[-1]["claim_absent"] else "supported"
    evidence = {
        "decision_ids": [str(decision.decision_id)],
        "provider": decision.provider,
        "fallback": decision.fallback,
    }
    if not decision.fallback and status == "uncertain":
        # A fallback can add information, never clear a primary uncertainty flag.
        review = await DecisionService(JevHttpAdapter(None), recorder, fallback).recommend(
            replace(request, decision_id=uuid4())
        )
        evidence["decision_ids"].append(str(review.decision_id))
        evidence.update(provider=review.provider, fallback=True)
    if decision.recommendation is Recommendation.REJECT:
        status = "unsupported"
    return status, evidence


@dataclass(frozen=True)
class ContentWriterService:
    store: object
    key: object
    model: ContentWriterModelAdapter | None
    primary: JevHttpAdapter
    decision_connection_factory: object
    github_credential: object | None = None
    github_transport: object | None = None
    runner: object | None = None
    candidate_connection_factory: object | None = None

    @property
    def configured(self):
        return isinstance(self.model, ContentWriterModelAdapter) and self.model.configured

    @property
    def candidate_configured(self):
        return callable(self.candidate_connection_factory) and all(
            value is not None
            for value in (self.github_credential, self.github_transport, self.runner)
        )

    async def draft(self, connection, *, session_token, generation, site_id, brief_id):
        projection = writer_call(connection, session_token, generation, site_id, "read")
        if not self.configured:
            return {"state": "unavailable", "reason": "MODEL_UNCONFIGURED"}
        prior = next((d for d in projection["drafts"] if d["brief_id"] == str(brief_id)), None)
        if prior:
            reason = prior["result"].get("reason", "")
            return {
                "state": "unavailable"
                if reason.startswith("MODEL_BUDGET_")
                else prior["result"]["state"],
                **({"reason": reason} if reason else {}),
                "draft_id": prior["draft_id"],
                "replayed": True,
            }
        if projection["cap_reached"]:
            return {"state": "cap_reached"}
        if projection["monthly_model_budget"]["state"] == "unavailable":
            return {"state": "unavailable", "reason": "MODEL_BUDGET_EXHAUSTED"}
        brief = next(
            (
                b
                for b in projection["briefs"]
                if b["brief_id"] == str(brief_id) and b["status"] == "accepted"
            ),
            None,
        )
        if brief is None:
            raise ContentWriterRejected("BRIEF_NOT_ACCEPTED")
        brain = read_brain(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
        )
        # The dedicated current-approved port, not a model or stale brief, supplies drafting facts.
        token_hash, generation = _session(session_token, generation)
        with _clean_transaction(connection):
            rows = connection.execute(
                "SELECT * FROM control.approved_business_brain_facts(%s,%s,%s)",
                (token_hash, generation, site_id),
            ).fetchall()
        approved = [{"fact_id": str(r[0]), "category": r[1], "statement": r[2]} for r in rows]
        selected = [f for f in approved if f["fact_id"] in brief["payload"]["fact_ids"]]
        if len(selected) != len(brief["payload"]["fact_ids"]):
            raise ContentWriterRejected("FACT_NOT_CURRENT_APPROVED")
        if len(canonical_json(selected)) > 6000:
            raise ContentWriterUnavailable("WRITER_FACT_CONTEXT_TOO_LARGE")
        inventory = writer_call(connection, session_token, generation, site_id, "inventory")
        if not inventory or len(inventory) > 32:
            raise ContentWriterUnavailable("ORIGINALITY_COVERAGE_UNAVAILABLE")
        sources = [
            read_crawl_text(
                connection,
                self.store,
                self.key,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                source_id=r["source_id"],
            )
            for r in inventory
        ]
        if not set(brief["payload"]["internal_links"]) <= {s["url"] for s in sources}:
            raise ContentWriterRejected("UNCRAWLED_INTERNAL_LINK")
        packet = {
            "brief": brief["payload"],
            "approved_facts": selected,
            "brand_voice": brain["voice"],
            "untrusted_sources": [
                {**s, "text": s["text"][:2000]}
                for s in sources
                if s["source_id"] in brief["payload"]["source_ids"]
            ],
            "voice_examples": voice_packet(
                sources,
                chosen_ids=brief["payload"]["source_ids"],
                ranked_ids=writer_call(
                    connection, session_token, generation, site_id, "voice_rank"
                ),
                profile=brain["voice"],
            ),
        }
        if len(canonical_json(packet)) > 48000:
            raise ContentWriterUnavailable("WRITER_INPUT_TOO_LARGE")
        intent = writer_call(
            connection,
            session_token,
            generation,
            site_id,
            "begin_draft",
            uuid4(),
            brief_id,
            hashlib.sha256(canonical_json(packet)).digest(),
            Jsonb(selected),
            Jsonb(brain["voice"]),
        )
        if intent["state"] != "started":
            return intent
        draft_id = UUID(intent["draft_id"])
        decision_id = None
        try:
            budget = PostgresModelBudget(connection, session_token, generation, site_id)
            model = replace(self.model, reasoner=replace(self.model.reasoner, budget=budget))
            result, quality, passes = await draft_with_quality(model, packet, draft_id)
            article = validate_article(result.output, brief["payload"]["internal_links"])
            extraction = await model.extract_claims(
                {"article": article, "approved_facts": selected},
                operation(draft_id, "claim-extraction"),
            )
            passes.append(model_receipt(extraction, "claim_check"))
            extracted = validate_claims(extraction.output, article)
            reviews = {}
            request = claim_question(article, selected)
            fallback = _EditorialFallback(model)
            with self.decision_connection_factory() as dc:
                recorder = PostgresDecisionRecorder(dc, Scope(UUID(intent["tenant_id"]), site_id))
                for path, item in article_sentences(article):
                    claims = extracted[path]
                    coverage = entailment_question(item["text"], claims, selected)
                    status, evidence = await _entailment(self.primary, recorder, fallback, coverage)
                    decision_id = UUID(evidence["decision_ids"][-1])
                    reviews[path] = {
                        "sentence": item["text"],
                        "coverage": status,
                        "coverage_evidence": evidence,
                        "claims": [],
                    }
                    for claim in claims:
                        supporting = [f for f in selected if f["fact_id"] in claim["fact_ids"]]
                        question = entailment_question(
                            item["text"], claims, supporting, claim=claim
                        )
                        status, evidence = await _entailment(
                            self.primary, recorder, fallback, question
                        )
                        reviews[path]["claims"].append({**claim, "status": status, **evidence})
                # Preserve the existing summary decision FK; it adds flags only.
                decision = await DecisionService(self.primary, recorder, fallback).recommend(
                    request
                )
                decision_id = decision.decision_id
            current = read_brain(
                connection,
                session_token=session_token,
                current_recovery_generation=generation,
                site_id=site_id,
            )
            current_facts = [f for f in current["facts"] if f["status"] == "approved"]
            extras = (
                [path for path, _ in article_sentences(article)]
                if any(r["claim_absent"] for r in fallback.receipts)
                or decision.recommendation is Recommendation.REJECT
                or decision.answers.get("claim_absent", {}).get("noul", 0) > 0.05
                else []
            )
            grounding = grounding_report(
                article,
                current_facts,
                selected_ids=set(brief["payload"]["fact_ids"]),
                extra_flags=extras,
                claim_reviews=reviews,
            )
            originality = originality_report(article, sources)
            payload = {
                "state": "rejected"
                if originality["state"] == "rejected"
                else (
                    "owner_required" if quality["state"] == "low_quality" else grounding["state"]
                ),
                "article": article,
                "grounding": grounding,
                "originality": originality,
                "quality": quality,
                "model_passes": passes,
                "claim_extraction": {
                    "output": extraction.output,
                    "receipt": model_receipt(extraction, "claim_check"),
                },
                "monthly_budget": budget.call("read"),
                "decision_id": str(decision_id),
                "provider": "openai_fallback" if fallback.receipts else decision.provider,
                "fallback": decision.fallback or bool(fallback.receipts),
                "draft_model": {
                    "response_id": result.provider_response_id,
                    "model": result.model_reported,
                    "usage": asdict(result.usage),
                },
                "review_models": fallback.receipts,
            }
        except (MetadataDraftError, ContentWriterRejected) as error:
            payload = {
                "state": "failed",
                "reason": str(getattr(error, "code", "WRITER_OUTPUT_INVALID")),
            }
        canonical = canonical_json(payload)
        outcome = writer_call(
            connection,
            session_token,
            generation,
            site_id,
            "finish_draft",
            draft_id,
            canonical,
            hashlib.sha256(canonical).digest(),
            decision_id,
        )
        if outcome not in {"completed", "replayed"}:
            raise ContentWriterUnavailable("DRAFT_COMPLETION_UNAVAILABLE")
        if payload.get("reason", "").startswith("MODEL_BUDGET_"):
            return {"state": "unavailable", "reason": payload["reason"], "draft_id": str(draft_id)}
        return {"state": payload["state"], "draft_id": str(draft_id)}

    async def seal(
        self, connection, *, session_token, generation, site_id, draft_id, extension_id, destination
    ):
        projection = writer_call(connection, session_token, generation, site_id, "read")
        prior = next((c for c in projection["candidates"] if c["draft_id"] == str(draft_id)), None)
        if prior:
            if prior["manifest"]["changed_files"][0]["path"] != destination or prior["manifest"][
                "extension_id"
            ] != str(extension_id):
                raise ContentWriterRejected("CANDIDATE_REQUEST_CONFLICT")
            return {"state": "replayed", "candidate_id": prior["candidate_id"]}
        if not self.candidate_configured:
            return {"state": "unavailable", "reason": "CANDIDATE_UNCONFIGURED"}
        draft = next((d for d in projection["drafts"] if d["draft_id"] == str(draft_id)), None)
        if not draft or draft["result"]["state"] not in {"grounded", "owner_required"}:
            raise ContentWriterRejected("DRAFT_NOT_SEALABLE")
        brief = next(b for b in projection["briefs"] if b["brief_id"] == draft["brief_id"])
        if brief["status"] != "accepted":
            raise ContentWriterRejected("BRIEF_SUPERSEDED")
        brain = read_brain(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
        )
        article = draft["result"]["article"]
        grounding = grounding_report(
            article,
            [f for f in brain["facts"] if f["status"] == "approved"],
            selected_ids=set(brief["payload"]["fact_ids"]),
            extra_flags=[
                s["path"] for s in draft["result"]["grounding"]["sentences"] if s["reasons"]
            ],
            claim_reviews={
                s["path"]: s["claim_review"]
                for s in draft["result"]["grounding"]["sentences"]
                if s.get("claim_review") is not None
            },
        )
        # Existing GitHub/build ports retain their identity role; writer ports use signal_api.
        with self.candidate_connection_factory() as candidate_connection:
            before, after, plan, build = await self._build(
                candidate_connection,
                session_token=session_token,
                generation=generation,
                site_id=site_id,
                extension_id=extension_id,
                draft_id=draft_id,
                article=article,
                kind=brief["payload"]["kind"],
                destination=destination,
            )
        manifest = {
            "schema_version": 1,
            "site_id": str(site_id),
            "draft_id": str(draft_id),
            "extension_id": str(extension_id),
            "build_id": str(build.id),
            "base_sha": plan.base_sha,
            "patch_sha256": plan.patch_sha256,
            "changed_files": [
                {
                    "path": destination,
                    "before": before.decode(),
                    "after": after.decode(),
                    "source_sha256": hashlib.sha256(before).hexdigest(),
                    "result_sha256": hashlib.sha256(after).hexdigest(),
                }
            ],
            "grounding": grounding,
            "originality": draft["result"]["originality"],
            "quality": draft["result"].get("quality"),
            "approval_class": "A2",
            "work_type": brief["payload"]["kind"],
            "threshold": 0.95,
            "autonomy_eligible": False,
            "expected_impact": "Evidence-grounded content for the accepted intent; "
            "no ranking guarantee.",
            "recovery_plan": "Owner-reviewed inverse patch against the exact base; "
            "no deletion or unpublish authority.",
            "build_receipt": asdict(build),
        }
        manifest["build_receipt"]["id"] = str(build.id)
        manifest["build_receipt"]["site_id"] = str(site_id)
        canonical = canonical_json(manifest)
        return writer_call(
            connection,
            session_token,
            generation,
            site_id,
            "seal",
            uuid5(NAMESPACE_URL, "content-candidate:" + str(draft_id)),
            draft_id,
            extension_id,
            build.id,
            canonical,
            hashlib.sha256(canonical).digest(),
        )

    async def _build(
        self,
        connection,
        *,
        session_token,
        generation,
        site_id,
        extension_id,
        draft_id,
        article,
        kind,
        destination,
    ):
        extension = read_github_pr_extension(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
            extension_id=extension_id,
        )
        current_extension = await inspect_current_github_pr_extension(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
            extension_id=extension_id,
            credential=self.github_credential,
            github_transport=self.github_transport,
        )
        binding = read_github_read_binding(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
            binding_id=extension.binding_id,
        )
        credentials = await self.github_credential.credentials()
        checkout = await checkout_github_repository(
            credentials=credentials, target=binding.target, transport=self.github_transport
        )
        if checkout.inventory.snapshot.base_sha != current_extension.base_sha:
            raise ContentWriterUnavailable("CONTENT_BASE_CHANGED")
        before, after, plan = render_article(
            article, extension, checkout, kind=kind, destination=destination
        )
        build = await build_candidate_from_github(
            connection,
            session_token=session_token,
            current_recovery_generation=generation,
            site_id=site_id,
            extension_id=extension_id,
            idempotency_key=uuid5(NAMESPACE_URL, "content-build:" + str(draft_id)),
            credential=self.github_credential,
            github_transport=self.github_transport,
            runner=self.runner,
            patch={destination: after},
            approved_paths=frozenset({destination}),
        )
        if (
            build.exit_class != "passed"
            or build.base_sha != plan.base_sha
            or build.patch_sha256 != plan.patch_sha256
        ):
            raise ContentWriterUnavailable("CONTENT_BUILD_FAILED")
        return before, after, plan, build
