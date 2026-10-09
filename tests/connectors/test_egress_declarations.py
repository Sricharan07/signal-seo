"""Replay the immutable pre-refactor HTTP-profile decisions, including denials."""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
from signal_core import egress_profiles
from signal_core.crawl_http import EgressHttpRequest, TelegramBotCredential

_CASES = json.loads((Path(__file__).parent / "fixtures" / "egress_0167_oracle.json").read_text())
_TYPES = {
    name: getattr(egress_profiles, name)
    for name in (
        "PageSpeedScope",
        "DataForSeoCredentialScope",
        "IndexNowSubmitScope",
        "Ga4ReadScope",
        "DrivePickedScope",
        "WebflowScope",
        "BingPageReadScope",
        "GitHubRepositoryWriteScope",
        "WordPressScope",
    )
}
_TYPES.update(EgressHttpRequest=EgressHttpRequest, TelegramBotCredential=TelegramBotCredential)


def _decode(value, profile_module=egress_profiles):
    if isinstance(value, list):
        return [_decode(item, profile_module) for item in value]
    if not isinstance(value, dict):
        return value
    if "type" in value:
        name = value["type"]
        assert name in _TYPES
        factory = (
            _TYPES[name]
            if name in {"EgressHttpRequest", "TelegramBotCredential"}
            else getattr(profile_module, name)
        )
        return factory(**_decode(value["fields"], profile_module))
    if "enum" in value:
        assert value["enum"] == "EgressProfile"
        return profile_module.EgressProfile(value["value"])
    if "uuid" in value:
        return UUID(value["uuid"])
    if "url_with_explicit_port" in value:
        url = value["url_with_explicit_port"]
        parts = urlsplit(url["base"])
        return urlunsplit(parts._replace(netloc=parts.netloc + ":" + url["port"]))
    if "hex" in value:
        return bytes.fromhex(value["hex"])
    if "repeat" in value:
        byte, count = value["repeat"]
        return bytes([byte]) * count
    if "tuple" in value:
        return tuple(_decode(item, profile_module) for item in value["tuple"])
    return {key: _decode(item, profile_module) for key, item in value.items()}


@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[
        f"{case['arguments']['profile']['value']}-{index}-{'allow' if case['allowed'] else 'deny'}"
        for index, case in enumerate(_CASES)
    ],
)
def test_pre_refactor_profile_decision(case):
    arguments = _decode(case["arguments"])
    if case["allowed"]:
        egress_profiles.validate_profile_request(**arguments)
    else:
        with pytest.raises(ValueError):
            egress_profiles.validate_profile_request(**arguments)


def test_every_http_profile_has_positive_and_negative_oracle_cases():
    counts = Counter((case["arguments"]["profile"]["value"], case["allowed"]) for case in _CASES)
    for profile in egress_profiles.EgressProfile:
        assert counts[profile.value, False], profile
        if profile != egress_profiles.EgressProfile.SMTP_SUBMIT:
            assert counts[profile.value, True], profile


@pytest.mark.parametrize("profile", tuple(egress_profiles.PROFILE_RULES))
def test_binding_declarations_retain_sql_ports_and_arguments(profile):
    scoped = {
        "webflow": ("bind_webflow_egress_profile", ("synthetic-site", "synthetic-collection")),
        "dataforseo": ("bind_dataforseo_egress_profile", ("synthetic-generation",)),
        "github_repository_write": (
            "bind_github_repository_write_profile",
            ("synthetic-owner/synthetic-repo",),
        ),
    }
    specialized = {
        "browser_worker_read": "bind_browser_read_profile",
        "drive_metadata": "bind_docs_egress_profile",
        "drive_export": "bind_docs_egress_profile",
        "openai_assistant": "bind_assistant_egress_profile",
        "perplexity_assistant": "bind_assistant_egress_profile",
        "gemini_assistant": "bind_assistant_egress_profile",
    }
    expected = scoped.get(
        profile.value,
        (specialized.get(profile.value, "bind_shared_egress_profile"), (profile.value,)),
    )
    request = SimpleNamespace(
        profile=profile,
        webflow_scope=SimpleNamespace(
            site_id="synthetic-site", collection_id="synthetic-collection"
        ),
        dataforseo_scope=SimpleNamespace(generation="synthetic-generation"),
        github_write_scope=SimpleNamespace(full_name="synthetic-owner/synthetic-repo"),
    )
    binding = egress_profiles.PROFILE_RULES[profile].binding
    assert (binding.function, binding.arguments(request)) == expected


@pytest.mark.parametrize(
    "name,origins,body,request_timeout,total_timeout",
    [
        ("slack", ("https://slack.com",), 262144, 5, 10),
        ("github", ("https://api.github.com",), 262144, 5, 10),
        ("gsc", ("https://oauth2.googleapis.com", "https://www.googleapis.com"), 262144, 5, 10),
        ("webflow", ("https://api.webflow.com", "https://webflow.com"), 131072, 5, 10),
        ("strategy_dataforseo", ("https://api.dataforseo.com",), 262144, 30, 30),
    ],
)
def test_factory_declarations_retain_origin_and_budget_bounds(
    name, origins, body, request_timeout, total_timeout
):
    rules = egress_profiles.CONNECTOR_FACTORY_RULES[name]
    policy = rules.policy()
    assert rules.origins == origins
    assert policy.allowed_origins == origins
    assert policy.max_body_bytes == body
    assert policy.request_timeout_seconds == request_timeout
    assert policy.total_timeout_seconds == total_timeout
    assert policy.max_redirects == 0
    with pytest.raises(ValueError):
        rules.policy(("https://unapproved.example.invalid",))
    with pytest.raises(ValueError):
        rules.policy(())


def test_pagespeed_context_declaration_is_not_a_new_authority():
    from signal_core.owner_connector_egress import OwnerConnectorContext, WeeklyPageSpeedContext

    for context_type in (OwnerConnectorContext, WeeklyPageSpeedContext):
        egress_profiles.validate_provider_context(
            egress_profiles.EgressProfile.PAGESPEED, object.__new__(context_type)
        )
    with pytest.raises(ValueError, match="reserved owner-site"):
        egress_profiles.validate_provider_context(egress_profiles.EgressProfile.PAGESPEED, object())
