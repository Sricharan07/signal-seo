import json
import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from signal_core.crawl_http import EgressHttpRequest
from signal_core.egress_profiles import EgressProfile, WordPressScope, profile_headers
from signal_core.shared_egress import SharedEgressRequest
from signal_core.wordpress_protocol import (
    WordPressCredential,
    WordPressUnavailable,
    least_privilege_user,
    marker,
    public_url,
    render_draft,
    rest,
)

ORIGIN = "https://cms.example.invalid"
CREDENTIAL = WordPressCredential("synthetic-author", "synthetic-application-password")
SCOPE = WordPressScope(ORIGIN, CREDENTIAL.authorization, 17, marker(uuid4()))


def request(path="posts", method="POST", document=None):
    if document is None:
        document = {
            "status": "draft",
            "slug": SCOPE.slug,
            "title": "Title",
            "content": "<p>Body</p>",
            "excerpt": "Summary",
        }
    return SharedEgressRequest(
        "connector",
        EgressHttpRequest(
            method,
            ORIGIN + "/wp-json/wp/v2/" + path,
            headers=profile_headers(EgressProfile.WORDPRESS_REST, method, CREDENTIAL.authorization),
            body=json.dumps(document).encode() if method == "POST" else b"",
            max_response_bytes=131072,
            timeout_seconds=10,
        ),
        EgressProfile.WORDPRESS_REST,
        wordpress_scope=SCOPE,
    )


@pytest.mark.parametrize(
    "path",
    [
        "posts/7",
        "posts/7/revisions",
        "pages",
        "media",
        "users",
        "settings",
        "plugins",
        "themes",
        "comments",
        "posts?_method=DELETE",
        "../wp/v2/posts",
        "%70osts",
    ],
)
def test_write_paths_closed(path):
    with pytest.raises(ValueError):
        request(path)


@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH", "OPTIONS"])
def test_methods_closed(method):
    with pytest.raises(ValueError):
        request(method=method)


@pytest.mark.parametrize("status", ["publish", "future", "private", "trash", "pending"])
def test_no_status_other_than_draft(status):
    with pytest.raises(ValueError):
        request(
            document={
                "status": status,
                "slug": SCOPE.slug,
                "title": "Title",
                "content": "Body",
                "excerpt": "",
            }
        )


def test_exact_reads_and_header_origin_json_negatives():
    request("users/me?context=edit", "GET")
    request(f"posts?slug={SCOPE.slug}&status=draft&context=edit&author=17&per_page=100", "GET")
    valid = request()
    assert "synthetic-application-password" not in repr(valid) + repr(SCOPE) + repr(CREDENTIAL)
    for changes in [
        {"url": "https://other.example.invalid/wp-json/wp/v2/posts"},
        {"headers": (*valid.http.headers, ("x-http-method-override", "DELETE"))},
        {"body": b"not-json"},
        {"body": b'{"status":"draft","status":"publish"}'},
        {
            "headers": tuple(
                (k, "text/plain" if k == "content-type" else v) for k, v in valid.http.headers
            )
        },
    ]:
        with pytest.raises(ValueError):
            replace(valid, http=replace(valid.http, **changes))
    for path in ["users/me", "posts/17?context=edit", "posts?status=publish", "posts?slug=other"]:
        with pytest.raises(ValueError):
            request(path, "GET")


@pytest.mark.parametrize(
    "role,cap",
    [
        ("administrator", "manage_options"),
        ("editor", "edit_others_posts"),
        ("author", "delete_others_posts"),
        ("author", "install_plugins"),
        ("author", "switch_themes"),
        ("author", "edit_users"),
        ("author", "manage_options"),
    ],
)
def test_excessive_capabilities(role, cap):
    with pytest.raises(WordPressUnavailable):
        least_privilege_user(
            {
                "id": 17,
                "roles": [role],
                "capabilities": {"read": True, "edit_posts": True, cap: True},
            }
        )


def test_author_and_custom_role_and_escaped_content():
    for role in ["author", "signal_writer"]:
        assert (
            least_privilege_user(
                {
                    "id": 17,
                    "roles": [role],
                    "capabilities": {"read": True, "edit_posts": True, role: True},
                }
            )["id"]
            == 17
        )
    tagged = {"text": "Founders use <drafts> & reviews.", "fact_ids": [str(uuid4())]}
    rendered = render_draft(
        {
            "title": tagged,
            "meta_description": tagged,
            "sections": [{"heading": tagged, "sentences": [tagged]}],
            "internal_links": [],
        }
    )
    assert rendered["status"] == "draft"
    assert "&lt;drafts&gt; &amp;" in rendered["content"]


def test_tls_and_credential_shape_required():
    with pytest.raises(ValueError):
        replace(SCOPE, origin="http://cms.example.invalid")
    for authorization in ["Basic Og==", "Basic YXV0aG9yOg==", "Bearer synthetic-token"]:
        with pytest.raises(ValueError):
            replace(SCOPE, authorization=authorization)


def test_recorded_post_read_and_core_plain_permalink():
    valid = request("users/me?context=edit", "GET")
    replace(
        valid,
        http=replace(valid.http, url=ORIGIN + "/wp-json/wp/v2/posts/7?context=edit"),
        wordpress_scope=replace(SCOPE, post_id=7),
    )
    assert public_url(ORIGIN + "/?p=7", ORIGIN, 7) == ORIGIN + "/?p=7"
    for url in [ORIGIN + "/?p=8", ORIGIN + "/?preview=true", "https://other.example.invalid/?p=7"]:
        with pytest.raises(WordPressUnavailable):
            public_url(url, ORIGIN, 7)


@pytest.mark.parametrize("body", [b'{"id":1,"id":2}', b'{"id":NaN}', b"{"])
def test_response_json_must_be_unambiguous(body):
    provider = SimpleNamespace(
        request_json=lambda **kwargs: SimpleNamespace(body=body, status_code=201)
    )
    with pytest.raises(WordPressUnavailable, match="WORDPRESS_RESPONSE_INVALID"):
        rest(provider, SCOPE, path="users/me?context=edit", operation_id=uuid4())


def test_delivery_harnesses_share_keys_under_alternate_pytest_module_loading(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from tests.delivery import test_wordpress_drafts as wordpress

    collected = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "delivery/test_autonomy_delivery.py"),
        run_name="synthetic_pytest_collection",
    )
    assert collected["_JOURNAL_KEY"] is wordpress._KEY
    assert collected["_JOURNAL_ENCRYPTION_KEY"] is wordpress._ENCRYPTION
