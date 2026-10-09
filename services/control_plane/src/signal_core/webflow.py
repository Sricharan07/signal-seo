"""Webflow v2's certified local subset: new primary-locale drafts, never CAS."""

import hashlib
import html
import json
import re
from datetime import datetime
from uuid import UUID

from signal_core.content_writer import validate_article
from signal_core.decision_contracts import canonical_json
from signal_core.json_objects import unique_object

SCOPES = frozenset({"cms:read", "cms:write", "sites:read"})
ATOMIC_UPDATE_PRECONDITION = False
ATOMIC_PUBLISH_PRECONDITION = False
CAPABILITIES = {
    "create_draft": "DRAFT_ONLY",
    "update": "WEBFLOW_UPDATE_ATOMIC_PRECONDITION_UNAVAILABLE",
    "publish": "WEBFLOW_PUBLISH_ATOMIC_PRECONDITION_UNAVAILABLE",
    "refresh": "WEBFLOW_EXISTING_ITEM_REFRESH_UNAVAILABLE",
    "production": "WEBFLOW_LIVE_QUALIFICATION_NOT_EXECUTED",
}


class WebflowUnavailable(Exception):
    """Closed, nonsecret failure codes, never upstream error text."""

    def __init__(self, code="WEBFLOW_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


def object_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{24}", value) is None:
        raise WebflowUnavailable("WEBFLOW_RESOURCE_REJECTED")
    return value


def secret(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._-]{20,4096}", value) is None:
        raise WebflowUnavailable("WEBFLOW_SECRET_UNAVAILABLE")
    return value


def document(body):
    try:
        result = json.loads(body, object_pairs_hook=unique_object)
        if not isinstance(result, dict):
            raise ValueError
        canonical_json(result)
        return result
    except (ValueError, TypeError, UnicodeError):
        raise WebflowUnavailable("WEBFLOW_RESPONSE_INVALID") from None


def validate_grant(payload, site):
    try:
        grant = payload["authorization"]
        scopes = grant["scope"].split(",")
        if (
            grant["grantType"] != "authorization_code"
            or len(scopes) != len(SCOPES)
            or set(scopes) != SCOPES
            or grant["authorizedTo"]["siteIds"] != [site]
            or grant["authorizedTo"].get("workspaceIds", [])
            or grant["authorizedTo"].get("userIds", [])
        ):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise WebflowUnavailable("WEBFLOW_OAUTH_SCOPE_REJECTED") from None


def validate_mapping(mapping, schema):
    if (
        not isinstance(mapping, dict)
        or set(mapping) != {"title", "description", "body"}
        or mapping["title"] != "name"
        or any(
            not isinstance(v, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", v) is None
            for v in mapping.values()
        )
        or len(set(mapping.values())) != 3
        or "slug" in mapping.values()
        or not isinstance(schema, list)
        or not 2 <= len(schema) <= 100
    ):
        raise WebflowUnavailable("WEBFLOW_FIELD_MAPPING_REJECTED")
    fields = {}
    try:
        for f in schema:
            key = f["slug"]
            if key in fields or type(f["isRequired"]) is not bool:
                raise ValueError
            fields[key] = f
        for source, target in mapping.items():
            field = fields[target]
            if (
                field["type"] != ("RichText" if source == "body" else "PlainText")
                or field["isEditable"] is not True
                or field.get("validations", {}) not in ({}, None)
            ):
                raise ValueError
        if fields["slug"]["type"] != "PlainText":
            raise ValueError
        if any(
            f["isRequired"] and key not in {*mapping.values(), "slug"} for key, f in fields.items()
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise WebflowUnavailable("WEBFLOW_COLLECTION_FORMAT_UNAVAILABLE") from None
    return mapping


def published_domain(payload, origin):
    try:
        domains = payload["customDomains"]
        if not isinstance(domains, list) or not 1 <= len(domains) <= 100:
            raise ValueError
        matches = [d for d in domains if "https://" + d["url"] == origin]
        if len(matches) != 1:
            raise ValueError
        stamp = datetime.fromisoformat(matches[0]["lastPublished"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise WebflowUnavailable("WEBFLOW_PUBLISHED_DOMAIN_MISMATCH") from None


def draft_body(article, mapping, operation_id: UUID):
    article = validate_article(article, [link["url"] for link in article.get("internal_links", [])])
    body = ""
    for section in article["sections"]:
        body += "<h2>" + html.escape(section["heading"]["text"]) + "</h2>"
        for sentence in section["sentences"]:
            body += "<p>" + html.escape(sentence["text"]) + "</p>"
    for link in article["internal_links"]:
        body += (
            '<p><a href="'
            + html.escape(link["url"], quote=True)
            + '">'
            + html.escape(link["anchor"])
            + "</a></p>"
        )
    title = article["title"]["text"]
    prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80].rstrip("-") or "article"
    slug = prefix + "-signal-" + operation_id.hex
    fields = {
        mapping["title"]: title,
        mapping["description"]: article["meta_description"]["text"],
        mapping["body"]: body,
        "slug": slug,
    }
    payload = {"items": [{"isDraft": True, "fieldData": fields}]}
    encoded = canonical_json(payload)
    if len(encoded) > 65536:
        raise WebflowUnavailable("WEBFLOW_CONTENT_TOO_LARGE")
    return payload


def reconcile_items(payload, expected):
    """Zero is deliberately unknown even after a prior positive read."""
    try:
        items = payload["items"]
        total = payload["pagination"]["total"]
        if not isinstance(items, list) or type(total) is not int or total < 0 or len(items) > 2:
            raise ValueError
        if total > 1 or len(items) > 1:
            return "ESCALATED", None
        if total == 0 and not items:
            return "OUTCOME_UNKNOWN", None
        if total != 1 or len(items) != 1:
            raise ValueError
        item = items[0]
        identifier = object_id(item["id"])
        if (
            item.get("isDraft") is not True
            or item.get("isArchived") is not False
            or item.get("lastPublished") is not None
            or item.get("fieldData") != expected["items"][0]["fieldData"]
        ):
            return "ESCALATED", identifier
        return "DRAFT_RECORDED", identifier
    except (KeyError, TypeError, ValueError):
        raise WebflowUnavailable("WEBFLOW_RECONCILIATION_INVALID") from None


def schema_digest(schema):
    return hashlib.sha256(canonical_json(schema)).hexdigest()
