"""Core REST contract: one new draft, never an existing-resource mutation."""

import base64
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import rfc8785

from signal_core.content_writer import validate_article
from signal_core.egress_profiles import EgressProfile, WordPressScope
from signal_core.json_objects import unique_object

UPDATES_UNAVAILABLE = "WordPress core REST has no atomic preconditions; needs a certified bridge"
PUBLISH_UNAVAILABLE = (
    "Publish in WordPress yourself; Signal cannot publish an exact revision safely via core REST"
)
QUARANTINE_REASON = (
    "The request may still create a draft. Signal will only look for it; it will not send it again."
)


class WordPressUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class WordPressCredential:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self):
        if (
            not all(
                isinstance(v, str)
                and 1 <= len(v) <= 256
                and all(ord(c) >= 32 and ord(c) < 127 for c in v)
                for v in (self.username, self.password)
            )
            or ":" in self.username
        ):
            raise WordPressUnavailable("WORDPRESS_CREDENTIAL_UNAVAILABLE")

    @property
    def authorization(self):
        return "Basic " + base64.b64encode(f"{self.username}:{self.password}".encode()).decode()


def least_privilege_user(value):
    allowed = {
        "read",
        "edit_posts",
        "edit_published_posts",
        "publish_posts",
        "upload_files",
        "delete_posts",
        "delete_published_posts",
        "level_0",
        "level_1",
        "level_2",
        "author",
    }
    try:
        roles, caps = value["roles"], value["capabilities"]
        if (
            type(value["id"]) is not int
            or value["id"] < 1
            or not isinstance(roles, list)
            or len(roles) != 1
            or roles[0] in {"administrator", "editor"}
            or not isinstance(roles[0], str)
            or len(roles[0]) > 64
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", roles[0]) is None
            or any(
                part in roles[0]
                for part in (
                    "plugin",
                    "theme",
                    "user",
                    "option",
                    "delete_others",
                    "manage_",
                    "edit_others",
                )
            )
            or not isinstance(caps, dict)
            or len(caps) > 64
            or any(type(v) is not bool for v in caps.values())
            or {k for k, v in caps.items() if v} - allowed - set(roles)
            or not all(caps.get(k) is True for k in ("read", "edit_posts"))
        ):
            raise ValueError
        return {"id": value["id"], "roles": roles, "capabilities": caps}
    except (KeyError, TypeError, ValueError):
        raise WordPressUnavailable("WORDPRESS_EXCESSIVE_CAPABILITIES") from None


def render_draft(article):
    article = validate_article(article, [link["url"] for link in article["internal_links"]])
    body = []
    for section in article["sections"]:
        body.append(f"<h2>{html.escape(section['heading']['text'])}</h2>")
        body.extend(f"<p>{html.escape(s['text'])}</p>" for s in section["sentences"])
    for link in article["internal_links"]:
        body.append(
            f'<p><a href="{html.escape(link["url"], quote=True)}">'
            f"{html.escape(link['anchor'])}</a></p>"
        )
    result = {
        "title": article["title"]["text"],
        "content": "\n".join(body),
        "excerpt": article["meta_description"]["text"],
        "status": "draft",
    }
    if len(rfc8785.dumps(result)) > 60000:
        raise WordPressUnavailable("WORDPRESS_CONTENT_TOO_LARGE")
    return result


def marker(intent_id: UUID):
    return "signal-s" + intent_id.hex


def content_digest(value):
    return hashlib.sha256(
        rfc8785.dumps({k: value[k] for k in ("title", "content", "excerpt")})
    ).hexdigest()


def post(value, *, author_id, slug, post_id=None):
    try:
        if (
            type(value["id"]) is not int
            or value["id"] < 1
            or type(value["author"]) is not int
            or value["author"] != author_id
            or not isinstance(value["slug"], str)
            or len(value["slug"]) > 200
            or post_id is None
            and value["slug"] != slug
            or post_id is not None
            and value["id"] != post_id
            or value["status"] not in {"draft", "publish"}
        ):
            raise ValueError
        fields = {k: value[k]["raw"] for k in ("title", "content", "excerpt")}
        if (
            any(not isinstance(v, str) for v in fields.values())
            or len(rfc8785.dumps(fields)) > 65536
        ):
            raise ValueError
        if (
            not isinstance(value["link"], str)
            or len(value["link"]) > 2048
            or not isinstance(value["modified_gmt"], str)
            or len(value["modified_gmt"]) > 64
        ):
            raise ValueError
        return {
            "post_id": value["id"],
            "status": value["status"],
            "content_sha256": content_digest(fields),
            "url": value["link"],
            "modified": value["modified_gmt"],
            "slug_changed": value["slug"] != slug,
        }
    except (KeyError, TypeError, ValueError):
        raise WordPressUnavailable("WORDPRESS_RESPONSE_INVALID") from None


def public_url(url, origin, post_id):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query not in {"", f"p={post_id}"}
            or origin != f"{parsed.scheme}://{parsed.netloc}"
            or not parsed.path.startswith("/")
            or any(ord(c) <= 32 for c in url)
        ):
            raise ValueError
        return url
    except (ValueError, TypeError):
        raise WordPressUnavailable("WORDPRESS_PUBLIC_URL_UNAVAILABLE") from None


def rest(egress, scope: WordPressScope, *, path, operation_id, document=None):
    response = egress.request_json(
        method="POST" if document is not None else "GET",
        url=scope.origin + "/wp-json/wp/v2/" + path,
        profile=EgressProfile.WORDPRESS_REST,
        wordpress_scope=scope,
        authorization=scope.authorization,
        body=rfc8785.dumps(document) if document is not None else b"",
        operation_id=operation_id,
        timeout_seconds=10,
        max_response_bytes=131072,
    )
    try:
        value = json.loads(response.body, object_pairs_hook=unique_object)
        json.dumps(value, allow_nan=False)
    except (ValueError, UnicodeError):
        raise WordPressUnavailable("WORDPRESS_RESPONSE_INVALID") from None
    return response.status_code, value
