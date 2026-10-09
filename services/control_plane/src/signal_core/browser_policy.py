"""Closed browser actions and exact-origin read scope, shared with the sandbox."""

import hashlib
import json
import math
from dataclasses import dataclass
from urllib.parse import urljoin

from signal_core.crawl_urls import CrawlScopePolicy, CrawlUrlRejected

ALLOWED_ACTIONS = frozenset(
    {"navigate", "follow_link", "scroll", "wait_network_idle", "read_tree", "screenshot"}
)
# Exact caller admission is mandatory as well. These families are never eligible,
# including subdomains, regional search domains and provider API origins.
DENIED_HOSTS = (
    "google",
    "bing.com",
    "yahoo",
    "duckduckgo.com",
    "search.brave.com",
    "yandex",
    "baidu.com",
    "ecosia.org",
    "startpage.com",
    "ask.com",
    "qwant.com",
    "you.com",
    "chatgpt.com",
    "openai.com",
    "perplexity.ai",
    "claude.ai",
    "anthropic.com",
    "gemini.google.com",
    "copilot.microsoft.com",
    "grok.com",
    "poe.com",
    "meta.ai",
)


class BrowserRejected(ValueError):
    """A browser operation is outside the closed read-only contract."""


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


@dataclass(frozen=True)
class BrowserLimits:
    steps: int = 8
    seconds: int = 60
    bytes: int = 5 * 1024 * 1024

    def __post_init__(self) -> None:
        for value, low, high in (
            (self.steps, 1, 20),
            (self.seconds, 1, 120),
            (self.bytes, 1024, 20 * 1024 * 1024),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise BrowserRejected("BROWSER_BUDGET_INVALID")


def admit_browser_url(policy: CrawlScopePolicy, value: str) -> str:
    try:
        url = policy.admit(value)
    except CrawlUrlRejected:
        raise BrowserRejected("BROWSER_ORIGIN_DENIED") from None
    labels = url.host.split(".")
    for blocked in DENIED_HOSTS:
        if "." not in blocked:
            denied = blocked in labels
        else:
            denied = url.host == blocked or url.host.endswith("." + blocked)
        if denied:
            raise BrowserRejected("BROWSER_CONSUMER_ORIGIN_DENIED")
    return url.fetch_url


def validate_action(action: object) -> dict:
    if not isinstance(action, dict) or action.get("action") not in ALLOWED_ACTIONS:
        raise BrowserRejected("BROWSER_ACTION_DENIED")
    kind = action["action"]
    field = {
        "navigate": "url",
        "follow_link": "id",
        "scroll": "pixels",
        "wait_network_idle": "milliseconds",
    }.get(kind)
    if set(action) != ({"action", field} if field else {"action"}):
        raise BrowserRejected("BROWSER_ACTION_INVALID")
    if field in {"url", "id"} and (
        not isinstance(action[field], str) or not 1 <= len(action[field]) <= 2048
    ):
        raise BrowserRejected("BROWSER_ACTION_INVALID")
    if field in {"pixels", "milliseconds"}:
        value = action[field]
        low, high = (-2000, 2000) if field == "pixels" else (1, 5000)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise BrowserRejected("BROWSER_ACTION_INVALID")
    return dict(action)


def reduce_links(nodes: list, page_url: str, policy: CrawlScopePolicy) -> list[dict]:
    """Deterministically prune AX link nodes, bounded before any decision call."""
    elements = []
    size = 0
    seen = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("role") != "link":
            continue
        href, text = node.get("url"), node.get("text")
        if not isinstance(href, str) or not isinstance(text, str):
            continue
        try:
            url = admit_browser_url(policy, urljoin(page_url, href))
        except BrowserRejected:
            continue
        text = " ".join(text.split())[:160]
        # Authentication/transaction links are not selectable even though GETs.
        if any(
            word in (text + " " + url).lower()
            for word in (
                "login",
                "log in",
                "sign in",
                "signin",
                "signup",
                "sign up",
                "checkout",
                "purchase",
                "captcha",
                "oauth",
                "logout",
                "sign out",
            )
        ):
            continue
        identity = digest([url, text])[:16]
        if identity in seen:
            continue
        element = {"id": "link_" + identity, "role": "link", "text": text, "url": url}
        size += len(json.dumps(element, ensure_ascii=True).encode())
        if len(elements) == 255 or size > 16 * 1024:
            break
        seen.add(identity)
        elements.append(element)
    return elements


def confident_choice(answer: object, elements: list[dict], threshold: float) -> str | None:
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    confidence = answer.get("confidence")
    probabilities = answer.get("probabilities")
    ids = {element["id"] for element in elements}
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not threshold <= confidence <= 1
        or answer.get("choice") not in ids
        or not isinstance(probabilities, dict)
        or set(probabilities) != ids
    ):
        return None
    if any(
        isinstance(p, bool)
        or not isinstance(p, (int, float))
        or not math.isfinite(p)
        or not 0 <= p <= 1
        for p in probabilities.values()
    ):
        return None
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-6):
        return None
    choice = answer["choice"]
    return choice if probabilities[choice] == max(probabilities.values()) else None
