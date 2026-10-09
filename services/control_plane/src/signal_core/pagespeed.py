"""Strict PSI projections. Lab diagnostics never stand in for field experience."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlencode
from uuid import UUID, uuid5

from signal_core.crawl_audit import CrawlAuditFinding
from signal_core.crawl_urls import normalize_crawl_url
from signal_core.egress_profiles import PageSpeedScope

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
MAX_RESPONSE_BYTES = 512 * 1024
TIMEOUT_SECONDS = 25
_NAMESPACE = UUID("702be4c3-bf88-4eaf-8b9f-85f18c9bbad1")
FIELD_METRICS = {
    "lcp": ("LARGEST_CONTENTFUL_PAINT_MS", 2500, 4000, 1, "ms"),
    "inp": ("INTERACTION_TO_NEXT_PAINT", 200, 500, 1, "ms"),
    "cls": ("CUMULATIVE_LAYOUT_SHIFT_SCORE", 0.1, 0.25, 100, "score"),
}
LAB_METRICS = {
    "lcp": ("largest-contentful-paint", "ms"),
    "fcp": ("first-contentful-paint", "ms"),
    "tbt": ("total-blocking-time", "ms"),
    "speed_index": ("speed-index", "ms"),
    "cls": ("cumulative-layout-shift", "score"),
}


class PageSpeedRejected(ValueError):
    def __init__(self, code="PSI_RESPONSE_REJECTED"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PageSpeedObservation:
    evidence_id: UUID
    url: str
    strategy: str
    lighthouse_version: str
    lab: dict
    field_url: dict
    field_origin: dict
    fetched_at: datetime
    response_sha256: bytes
    findings: tuple[CrawlAuditFinding, ...]

    def projection(self) -> dict:
        return {
            "evidence_id": str(self.evidence_id),
            "url": self.url,
            "strategy": self.strategy,
            "lighthouse_version": self.lighthouse_version,
            "lab": self.lab,
            "field_url": self.field_url,
            "field_origin": self.field_origin,
            "fetched_at": self.fetched_at.isoformat(),
            "response_sha256": self.response_sha256.hex(),
            "findings": [finding.to_json() for finding in self.findings],
        }


def pagespeed_url(scope: PageSpeedScope, url: str, strategy: str) -> str:
    page = normalize_crawl_url(url)
    if page.origin != scope.verified_origin or page.fetch_url != url:
        raise PageSpeedRejected("PSI_SCOPE_REJECTED")
    if strategy not in {"mobile", "desktop"}:
        raise PageSpeedRejected("PSI_STRATEGY_REJECTED")
    query = [("url", url), ("strategy", strategy), ("category", "performance")]
    if scope.api_key is not None:
        query.append(("key", scope.api_key))
    return ENDPOINT + "?" + urlencode(query)


def _pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError()
        document[key] = value
    return document


def _number(value, maximum=10**9):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError()
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError()
    return parsed.astimezone(UTC)


def _period(experience):
    value = experience.get("collectionPeriod")
    if value is None:
        return {
            "state": "unavailable",
            "reason": "collection_period_not_reported",
            "first_date": None,
            "last_date": None,
        }
    if not isinstance(value, dict) or set(value) != {"firstDate", "lastDate"}:
        raise ValueError()

    def day(item):
        if not isinstance(item, dict) or set(item) != {"year", "month", "day"}:
            raise ValueError()
        if any(type(number) is not int for number in item.values()):
            raise ValueError()
        return date(item["year"], item["month"], item["day"])

    first, last = day(value["firstDate"]), day(value["lastDate"])
    if not 0 <= (last - first).days <= 31:
        raise ValueError()
    return {
        "state": "available",
        "reason": None,
        "first_date": first.isoformat(),
        "last_date": last.isoformat(),
    }


def _field(value, source, locator):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError()
    metrics = value.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError()
    identity = value.get("id")
    reason = None
    if value.get("origin_fallback") is True and source == "url":
        reason = "origin_fallback_not_page_data"
    elif identity is not None and identity.rstrip("/") != locator.rstrip("/"):
        raise ValueError()
    elif not metrics:
        reason = "insufficient_field_data" if value else "field_data_not_reported"
    result = {
        "source": source,
        "locator": locator,
        "percentile": 75,
        "collection_period": _period(value),
        "metrics": {},
    }
    for name, (key, good, poor, scale, unit) in FIELD_METRICS.items():
        item = metrics.get(key)
        if reason or item is None:
            result["metrics"][name] = {
                "state": "unavailable",
                "reason": reason or "metric_not_reported",
                "value": None,
                "unit": unit,
                "rating": None,
            }
            continue
        if not isinstance(item, dict) or identity is None:
            raise ValueError()
        if "percentile" not in item:
            result["metrics"][name] = {
                "state": "unavailable",
                "reason": "percentile_not_reported",
                "value": None,
                "unit": unit,
                "rating": None,
            }
            continue
        number = _number(item["percentile"]) / scale
        if name == "cls" and number > 100:
            raise ValueError()
        result["metrics"][name] = {
            "state": "available",
            "reason": None,
            "value": number,
            "unit": unit,
            "rating": "good"
            if number <= good
            else "poor"
            if number > poor
            else "needs_improvement",
        }
    ratings = [metric["rating"] for metric in result["metrics"].values()]
    result["status"] = (
        "unavailable"
        if None in ratings
        else (
            "poor"
            if "poor" in ratings
            else "needs_improvement"
            if "needs_improvement" in ratings
            else "good"
        )
    )
    return result


def parse_pagespeed(
    body: bytes, *, url: str, strategy: str, evidence_id: UUID, fetched_at: datetime
) -> PageSpeedObservation:
    try:
        if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_RESPONSE_BYTES:
            raise ValueError()
        if not isinstance(evidence_id, UUID) or strategy not in {"mobile", "desktop"}:
            raise ValueError()
        _time(fetched_at.isoformat())
        document = json.loads(
            body,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict) or document.get("id") != url:
            raise ValueError()
        lighthouse = document["lighthouseResult"]
        if not isinstance(lighthouse, dict):
            raise ValueError()
        version = lighthouse["lighthouseVersion"]
        if (
            not isinstance(version, str)
            or re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", version) is None
        ):
            raise ValueError()
        if (
            lighthouse["requestedUrl"] != url
            or lighthouse["finalUrl"] != url
            or lighthouse["configSettings"]["formFactor"] != strategy
        ):
            raise ValueError()
        _time(lighthouse["fetchTime"])
        audits = lighthouse["audits"]
        if not isinstance(audits, dict):
            raise ValueError()
        lab = {
            "source": "psi_lighthouse",
            "status": "available",
            "reason": None,
            "run_at": lighthouse["fetchTime"],
            "performance_score": None,
            "metrics": {},
        }
        runtime_error = lighthouse.get("runtimeError")
        if runtime_error is not None:
            if not isinstance(runtime_error, dict) or not isinstance(
                runtime_error.get("code"), str
            ):
                raise ValueError()
            lab.update(status="unavailable", reason="lighthouse_runtime_error")
        score = lighthouse.get("categories", {}).get("performance", {}).get("score")
        if score is not None:
            lab["performance_score"] = _number(score, 1) if runtime_error is None else None
        for name, (key, unit) in LAB_METRICS.items():
            item = audits.get(key)
            if item is not None and not isinstance(item, dict):
                raise ValueError()
            number = item.get("numericValue") if item else None
            lab["metrics"][name] = {
                "state": "unavailable" if number is None or runtime_error else "available",
                "value": _number(number) if number is not None and not runtime_error else None,
                "unit": unit,
                "reason": "lighthouse_runtime_error"
                if runtime_error
                else "metric_not_reported"
                if number is None
                else None,
            }
        page_field = _field(document.get("loadingExperience"), "url", url)
        origin_field = _field(
            document.get("originLoadingExperience"), "origin", normalize_crawl_url(url).origin
        )
        findings = []
        for field in (page_field, origin_field):
            for name, metric in field["metrics"].items():
                if metric["rating"] != "poor":
                    continue
                key = f"performance.cwv.{field['source']}.{name}.poor"
                findings.append(
                    CrawlAuditFinding(
                        uuid5(_NAMESPACE, f"{evidence_id}:{key}"),
                        key,
                        f"Poor {name.upper()}",
                        f"CrUX {field['source']} {strategy} p75 {name.upper()} "
                        "exceeds the poor threshold. "
                        "Observation only; no automated fix is available.",
                        "medium",
                        field["locator"],
                        "pagespeed_observation",
                        evidence_id,
                    )
                )
        return PageSpeedObservation(
            evidence_id,
            url,
            strategy,
            version,
            lab,
            page_field,
            origin_field,
            fetched_at,
            hashlib.sha256(body).digest(),
            tuple(findings),
        )
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        RecursionError,
        OverflowError,
    ):
        raise PageSpeedRejected() from None
