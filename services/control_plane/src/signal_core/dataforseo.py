"""Typed, untrusted research data; no scraping, interpretation, or authority."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit

from signal_core.dataforseo_credentials import DataForSeoUnavailable
from signal_core.egress_profiles import DATAFORSEO_ENDPOINTS, validate_dataforseo_body

Kind = Literal["serp", "volume", "backlinks"]
MAX_RESPONSE_BYTES = 128 * 1024
# Snapshot 2026-10-02: one base SERP, one Google Ads task, one summary row.
DOCUMENTED_COST_MICROS = {"serp": 2000, "volume": 90000, "backlinks": 24036}


@dataclass(frozen=True)
class DataForSeoQuery:
    kind: Kind
    subject: str
    location_code: int | None = None
    language_code: str | None = None

    def body(self) -> bytes:
        if self.kind not in DATAFORSEO_ENDPOINTS:
            raise DataForSeoUnavailable("DATAFORSEO_QUERY_INVALID")
        if self.kind == "backlinks":
            if self.location_code is not None or self.language_code is not None:
                raise DataForSeoUnavailable("DATAFORSEO_QUERY_INVALID")
            task = {"target": self.subject, "include_subdomains": True}
        else:
            task = {"location_code": self.location_code, "language_code": self.language_code}
            if self.kind == "serp":
                task.update(keyword=self.subject, depth=10, max_crawl_pages=1)
            else:
                task["keywords"] = [self.subject]
        body = json.dumps([task], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        try:
            validate_dataforseo_body(DATAFORSEO_ENDPOINTS[self.kind], body)
        except ValueError:
            raise DataForSeoUnavailable("DATAFORSEO_QUERY_INVALID") from None
        return body

    @property
    def endpoint(self) -> str:
        return "https://api.dataforseo.com" + DATAFORSEO_ENDPOINTS[self.kind]


@dataclass(frozen=True)
class DataForSeoResult:
    task_id: str
    cost_micros: int
    response_sha256: bytes
    data: dict


def reported_cost(query: DataForSeoQuery, body: bytes) -> int | None:
    """Cost survives a malformed result, but an untrusted envelope cannot settle a hold."""
    try:
        if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
            return None
        document = json.loads(
            body, parse_float=Decimal, parse_constant=lambda _: _reject(), object_pairs_hook=_object
        )
        tasks = document["tasks"]
        if (
            not isinstance(tasks, list)
            or len(tasks) != 1
            or type(document["tasks_count"]) is not int
            or document["tasks_count"] != 1
        ):
            return None
        task = tasks[0]
        cost = _cost(document["cost"])
        if _cost(task["cost"]) != cost or task["path"] != DATAFORSEO_ENDPOINTS[query.kind].strip(
            "/"
        ).split("/"):
            return None
        if any(
            task["data"].get(key) != value for key, value in json.loads(query.body())[0].items()
        ):
            return None
        return cost
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidOperation):
        return None


def _reject() -> None:
    raise ValueError


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _integer(value, maximum=10**12):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError
    return value


def _matches(actual, expected) -> bool:
    return type(actual) is type(expected) and actual == expected


def _cost(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError
    decimal = Decimal(value)
    micros = decimal * 1000000
    if (
        not decimal.is_finite()
        or not 0 <= micros <= 100000000
        or micros != micros.to_integral_value()
    ):
        raise ValueError
    return int(micros)


def parse_response(query: DataForSeoQuery, body: bytes) -> DataForSeoResult:
    """Ignore non-consumed metadata; strictly validate every identity and consumed field."""
    try:
        if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_RESPONSE_BYTES:
            raise ValueError
        document = json.loads(
            body, parse_float=Decimal, parse_constant=lambda _: _reject(), object_pairs_hook=_object
        )
        if (
            not isinstance(document, dict)
            or type(document.get("status_code")) is not int
            or document["status_code"] != 20000
        ):
            raise ValueError
        cost = _cost(document["cost"])
        if (
            type(document.get("tasks_count")) is not int
            or document["tasks_count"] != 1
            or type(document.get("tasks_error")) is not int
            or document["tasks_error"] != 0
        ):
            raise ValueError
        tasks = document["tasks"]
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
            raise ValueError
        task = tasks[0]
        task_id = task["id"]
        if not isinstance(task_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) is None:
            raise ValueError
        if (
            type(task.get("status_code")) is not int
            or task["status_code"] != 20000
            or _cost(task["cost"]) != cost
        ):
            raise ValueError
        if task.get("path") != DATAFORSEO_ENDPOINTS[query.kind].strip("/").split("/"):
            raise ValueError
        echoed = task["data"]
        requested = json.loads(query.body())[0]
        if not isinstance(echoed, dict) or any(
            not _matches(echoed.get(key), value) for key, value in requested.items()
        ):
            raise ValueError
        result = task["result"]
        if (
            type(task.get("result_count")) is not int
            or task["result_count"] != 1
            or not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], dict)
        ):
            raise ValueError
        item = result[0]
        data = {
            "kind": query.kind,
            "subject": query.subject,
            "location_code": query.location_code,
            "language_code": query.language_code,
        }
        if query.kind == "backlinks":
            if item.get("target") != query.subject:
                raise ValueError
            data.update(
                backlinks=_integer(item["backlinks"]),
                referring_domains=_integer(item["referring_domains"]),
                rank=_integer(item["rank"], 1000),
            )
        elif query.kind == "volume":
            if any(
                not _matches(item.get(key), value)
                for key, value in {
                    "keyword": query.subject,
                    "location_code": query.location_code,
                    "language_code": query.language_code,
                }.items()
            ):
                raise ValueError
            data["search_volume"] = (
                None if item.get("search_volume") is None else _integer(item["search_volume"])
            )
            if "search_volume" not in item:
                raise ValueError
        else:
            if any(
                not _matches(item.get(key), value)
                for key, value in {
                    "keyword": query.subject,
                    "location_code": query.location_code,
                    "language_code": query.language_code,
                }.items()
            ):
                raise ValueError
            items = item["items"]
            if (
                not isinstance(items, list)
                or len(items) > 100
                or type(item.get("items_count")) is not int
                or item["items_count"] != len(items)
            ):
                raise ValueError
            competitors = []
            ranks = set()
            for organic in items:
                if not isinstance(organic, dict) or not isinstance(organic.get("type"), str):
                    raise ValueError
                if organic["type"] != "organic":
                    continue
                rank = _integer(organic["rank_group"], 10)
                url = organic["url"]
                domain = organic["domain"]
                if (
                    rank < 1
                    or rank in ranks
                    or not isinstance(url, str)
                    or len(url) > 2048
                    or not isinstance(domain, str)
                ):
                    raise ValueError
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or parsed.username
                    or parsed.password
                    or parsed.hostname != domain
                    or parsed.netloc != domain
                    or re.fullmatch(r"[a-z0-9.-]{1,253}", domain) is None
                    or any(ord(c) < 32 for c in url)
                ):
                    raise ValueError
                ranks.add(rank)
                competitors.append({"rank": rank, "url": url, "domain": domain})
            data["competitors"] = sorted(competitors, key=lambda item: item["rank"])
        return DataForSeoResult(task_id, cost, hashlib.sha256(body).digest(), data)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, InvalidOperation):
        raise DataForSeoUnavailable("DATAFORSEO_RESPONSE_REJECTED") from None
