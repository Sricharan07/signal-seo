import hashlib
import json
import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SIGNAL_CRAWLER_NETWORK_LAB") != "1",
    reason="Run through scripts/run-crawler-network-tests.py.",
)

IMAGE = os.environ.get("SIGNAL_CRAWLER_NETWORK_IMAGE", "")
NETWORK = os.environ.get("SIGNAL_CRAWLER_NETWORK_NAME", "")
RUN_ID = os.environ.get("SIGNAL_CRAWLER_NETWORK_RUN_ID", "")
SERVER_ADDRESS = os.environ.get("SIGNAL_CRAWLER_NETWORK_ADDRESS", "")
LABEL = "dev.signal.crawler-network-lab"


def test_live_verification_uses_real_bounded_get_bytes_and_rejects_redirect_timeout():
    from signal_core.live_verification import LivePostcondition, verify_live_html

    program = f"""
import json
from signal_core.crawl_http import (
    EgressHttpRequest, PinnedHttpFetcher, CrawlFetchUnavailable
)
from signal_core.crawl_urls import CrawlScopePolicy
calls=[]
def resolver(host, port, timeout):
    calls.append(host)
    return [{SERVER_ADDRESS!r}]
fetcher=PinnedHttpFetcher(resolver)
policy=CrawlScopePolicy(schema_version=1, allowed_origins=("http://live.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)", max_redirects=0,
    max_body_bytes=131072, request_timeout_seconds=1, total_timeout_seconds=2)
result=fetcher.request(EgressHttpRequest("GET","http://live.example/",headers=(("accept","text/html"),),accepted_media_types=("text/html",),max_response_bytes=131072,timeout_seconds=1),policy=policy)
stale=fetcher.request(EgressHttpRequest("GET","http://live.example/stale",accepted_media_types=("text/html",)),policy=policy)
redirect=fetcher.request(EgressHttpRequest("GET","http://live.example/redirect",accepted_media_types=("text/html",),timeout_seconds=1),policy=policy)
assert redirect.outcome=="redirect_rejected" and redirect.http_status==302 and not redirect.body
try:
    fetcher.request(EgressHttpRequest("GET","http://live.example/timeout",accepted_media_types=("text/html",),timeout_seconds=1),policy=policy)
except CrawlFetchUnavailable:
    pass
else:
    raise AssertionError("Timed-out live fetch was accepted")
assert all(host=="live.example" for host in calls)
print(json.dumps({{"body":result.body.decode(),"stale":stale.body.decode(),"digest":result.body_sha256,"status":result.http_status,"url":result.final_url}}))
"""
    execution = run_program(program, check=False)
    assert execution.returncode == 0, execution.stderr
    record = json.loads(execution.stdout)
    body, stale = record["body"].encode(), record["stale"].encode()
    assert hashlib.sha256(body).hexdigest() == record["digest"]
    contract = LivePostcondition(
        "technical_description",
        record["url"],
        hashlib.sha256(stale).hexdigest(),
        record["digest"],
        {"meta_description": "Exact evidence"},
        "Inverse patch with conflict review.",
    )
    assert (
        verify_live_html(
            contract, body=body, http_status=record["status"], final_url=record["url"]
        ).outcome
        == "verified"
    )
    assert (
        verify_live_html(contract, body=stale, http_status=200, final_url=record["url"]).reason
        == "EC_077_STALE_PAGE"
    )


def docker(*args, check=True, timeout=30):
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_program(program, *, check=True):
    return docker(
        "run",
        "--rm",
        "--network",
        NETWORK,
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=1m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "64",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--label",
        f"{LABEL}={RUN_ID}",
        IMAGE,
        "-c",
        program,
        check=check,
    )


def test_lab_image_and_network_are_nonroot_private_and_internal():
    image = json.loads(docker("image", "inspect", IMAGE).stdout)[0]
    network = json.loads(docker("network", "inspect", NETWORK).stdout)[0]

    assert image["Config"]["User"] == "10001:10001"
    assert image["Config"]["Entrypoint"] == ["python"]
    assert network["Internal"] is True
    assert network["IPAM"]["Config"] == [
        {
            "Subnet": os.environ["SIGNAL_CRAWLER_NETWORK_SUBNET"],
            "Gateway": os.environ["SIGNAL_CRAWLER_NETWORK_GATEWAY"],
        }
    ]
    assert network["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert network["Options"]["com.docker.network.bridge.enable_ip_masquerade"] == "false"


def test_real_http_fetch_pins_address_revalidates_redirect_and_sends_no_authority():
    program = f"""
import json
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://crawl.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    max_body_bytes=1024,
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
redirected = fetcher.fetch("http://CRAWL.example/start#client", policy=policy)
echo = fetcher.fetch("http://crawl.example/echo", policy=policy)
print(json.dumps({{
    "body": redirected.body.decode(),
    "final_url": redirected.final_url,
    "original_url": redirected.original_url,
    "redirect_chain": redirected.redirect_chain,
    "resolved_address": redirected.resolved_address,
    "request": json.loads(echo.body),
}}))
"""
    result = run_program(program)
    record = json.loads(result.stdout)

    assert record == {
        "body": "<html><title>Lab</title></html>",
        "final_url": "http://crawl.example/final?b=2&a=1",
        "original_url": "http://CRAWL.example/start#client",
        "redirect_chain": ["http://crawl.example/final?b=2&a=1"],
        "resolved_address": SERVER_ADDRESS,
        "request": {
            "accept_encoding": "identity",
            "authorization": None,
            "cookie": None,
            "host": "crawl.example",
            "user_agent": "SignalBot/1.0 (+https://signal.example/bot)",
        },
    }
    assert result.stderr == ""


def test_real_private_redirect_is_rejected_before_a_second_connection():
    program = f"""
from signal_core.crawl_http import CrawlFetchRejected, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

calls = []
def resolve(host, port, timeout):
    calls.append(host)
    return [{SERVER_ADDRESS!r}]

fetcher = PinnedHttpFetcher(resolve)
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://crawl.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
try:
    fetcher.fetch("http://crawl.example/private-redirect", policy=policy)
except CrawlFetchRejected:
    assert calls == ["crawl.example"]
else:
    raise AssertionError("private redirect was not rejected")
"""
    result = run_program(program)
    assert result.stdout == ""
    assert result.stderr == ""


def test_real_provider_post_is_pinned_bounded_and_does_not_add_cookies():
    program = f"""
import json
from signal_core.crawl_http import EgressHttpRequest, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://provider.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
request = EgressHttpRequest(
    "POST",
    "http://provider.example/v1/decision",
    headers=(
        ("accept", "application/json"),
        ("authorization", "Bearer isolated-network-secret"),
        ("content-type", "application/json"),
    ),
    body=b'{{"kind":"synthetic"}}',
    timeout_seconds=2,
)
result = fetcher.request(request, policy=policy)
print(json.dumps({{
    "body": json.loads(result.body),
    "final_url": result.final_url,
    "outcome": result.outcome,
    "resolved_address": result.resolved_address,
    "status": result.http_status,
}}))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == {
        "body": {
            "authorization_present": True,
            "content_type": "application/json",
            "cookie": None,
            "submitted": {"kind": "synthetic"},
        },
        "final_url": "http://provider.example/v1/decision",
        "outcome": "fetched",
        "resolved_address": SERVER_ADDRESS,
        "status": 200,
    }
    assert "isolated-network-secret" not in result.stdout
    assert result.stderr == ""


def test_real_model_api_key_header_is_pinned_and_never_echoed():
    program = f"""
import json
from signal_core.crawl_http import EgressHttpRequest, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://provider.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
request = EgressHttpRequest(
    "POST", "http://provider.example/v1/assistant",
    headers=(("accept", "application/json"),
             ("content-type", "application/json"),
             ("x-goog-api-key", "synthetic-google-key-00000000")),
    body=b'{{"kind":"synthetic"}}', timeout_seconds=2,
)
result = fetcher.request(request, policy=policy)
print(json.dumps(json.loads(result.body)))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == {
        "authorization_present": False,
        "content_type": "application/json",
        "cookie": None,
        "google_api_key_present": True,
        "submitted": {"kind": "synthetic"},
    }
    assert "synthetic-google-key" not in result.stdout
    assert result.stderr == ""


def test_real_connector_get_and_sensitive_form_post_are_pinned_and_bounded():
    program = f"""
import json
from signal_core.crawl_http import EgressHttpRequest, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://provider.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
sites = fetcher.request(EgressHttpRequest(
    "GET", "http://provider.example/webmasters/v3/sites",
    headers=(("accept", "application/json"),), timeout_seconds=2,
), policy=policy)
token = fetcher.request(EgressHttpRequest(
    "POST", "http://provider.example/token",
    headers=(("accept", "application/json"),
             ("content-type", "application/x-www-form-urlencoded")),
    body=b"grant_type=authorization_code&client_secret=synthetic-secret&code_verifier=synthetic-verifier",
    timeout_seconds=2,
), policy=policy)
print(json.dumps({{
    "sites": json.loads(sites.body), "token": json.loads(token.body),
    "get_peer": sites.resolved_address, "post_peer": token.resolved_address,
    "get_outcome": sites.outcome, "post_outcome": token.outcome,
}}))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == {
        "sites": {"siteEntry": []},
        "token": {
            "grant_type": ["authorization_code"],
            "has_client_secret": True,
            "has_code_verifier": True,
            "cookie": None,
        },
        "get_peer": SERVER_ADDRESS,
        "post_peer": SERVER_ADDRESS,
        "get_outcome": "fetched",
        "post_outcome": "fetched",
    }
    assert "synthetic-secret" not in result.stdout
    assert result.stderr == ""


def test_real_bing_style_bearer_get_is_pinned_and_does_not_forward_cookies():
    program = f"""
import json
from signal_core.crawl_http import EgressHttpRequest, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://provider.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
result = fetcher.request(EgressHttpRequest(
    "GET", "http://provider.example/webmaster/api.svc/json/GetLinkCounts?siteUrl=http%3A%2F%2Fexample.com%2F&page=0",
    headers=(("accept", "application/json"),
             ("authorization", "Bearer synthetic-bing-secret")),
    timeout_seconds=2,
), policy=policy)
print(json.dumps({{"body": json.loads(result.body), "peer": result.resolved_address,
                  "outcome": result.outcome}}))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == {
        "body": {
            "method": "GetLinkCounts",
            "authorization_present": True,
            "cookie": None,
        },
        "peer": SERVER_ADDRESS,
        "outcome": "fetched",
    }
    assert "synthetic-bing-secret" not in result.stdout
    assert result.stderr == ""


def test_real_provider_redirect_is_recorded_without_following():
    program = f"""
from signal_core.crawl_http import EgressHttpRequest, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

calls = []
def resolve(host, port, timeout):
    calls.append(host)
    return [{SERVER_ADDRESS!r}]

fetcher = PinnedHttpFetcher(resolve)
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://provider.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
result = fetcher.request(
    EgressHttpRequest(
        "POST",
        "http://provider.example/v1/private-redirect",
        headers=(("content-type", "application/json"),),
        body=b'{{}}',
        timeout_seconds=2,
    ),
    policy=policy,
)
assert result.outcome == "redirect_rejected"
assert result.http_status == 307
assert result.body == b""
assert calls == ["provider.example"]
"""
    result = run_program(program)
    assert result.stdout == ""
    assert result.stderr == ""


def test_real_plaintext_proof_is_exact_and_redirect_free():
    program = f"""
from signal_core.crawl_http import CrawlFetchRejected, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://proof.example", "http://proof-redirect.example"),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    max_redirects=0,
    max_body_bytes=1024,
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
path = "/.well-known/signal-site-verification.txt"
proof = fetcher.fetch_text("http://proof.example" + path, policy=policy)
assert proof.body == b"signal-site-verification=network-lab\\n"
assert proof.media_type == "text/plain"
assert proof.redirect_chain == ()
try:
    fetcher.fetch_text("http://proof-redirect.example" + path, policy=policy)
except CrawlFetchRejected:
    pass
else:
    raise AssertionError("origin proof redirect was not rejected")
"""
    result = run_program(program)
    assert result.stdout == ""
    assert result.stderr == ""


def test_real_streaming_limit_and_encoding_fail_without_retaining_body():
    program = f"""
import json
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://crawl.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    max_body_bytes=1024,
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
records = [
    fetcher.fetch("http://crawl.example/oversized", policy=policy),
    fetcher.fetch("http://crawl.example/compressed", policy=policy),
]
print(json.dumps([[item.outcome, item.decoded_bytes, item.body_sha256] for item in records]))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == [
        ["body_limit", 0, None],
        ["unsupported_encoding", 0, None],
    ]
    assert result.stderr == ""


def test_internal_lab_network_cannot_reach_an_unserved_public_destination():
    program = f"""
from signal_core.crawl_http import CrawlFetchUnavailable, PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

fetcher = PinnedHttpFetcher(lambda host, port, timeout:
    [{os.environ["SIGNAL_CRAWLER_NETWORK_UNSERVED_ADDRESS"]!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://outside.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=1,
    total_timeout_seconds=2,
)
try:
    fetcher.fetch("http://outside.example/", policy=policy)
except CrawlFetchUnavailable:
    pass
else:
    raise AssertionError("isolated network unexpectedly reached another destination")
"""
    result = run_program(program)
    assert result.stdout == ""
    assert result.stderr == ""


def test_real_robots_fetch_follows_admitted_redirect_and_classifies_statuses():
    program = f"""
import json
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

origins = (
    "http://robots.example",
    "http://robots-redirect.example",
    "http://robots-missing.example",
    "http://robots-forbidden.example",
    "http://robots-backoff.example",
    "http://robots-failure.example",
)
fetcher = PinnedHttpFetcher(lambda host, port, timeout: [{SERVER_ADDRESS!r}])
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=origins,
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
records = [fetcher.fetch_robots(origin, policy=policy) for origin in origins[1:]]
print(json.dumps([[item.outcome, item.final_url, item.redirect_chain,
                   item.decoded_bytes] for item in records]))
"""
    result = run_program(program)
    assert json.loads(result.stdout) == [
        [
            "fetched",
            "http://robots.example/robots.txt",
            ["http://robots.example/robots.txt"],
            41,
        ],
        ["not_found", "http://robots-missing.example/robots.txt", [], 0],
        ["forbidden", "http://robots-forbidden.example/robots.txt", [], 0],
        ["backoff", "http://robots-backoff.example/robots.txt", [], 0],
        ["server_error", "http://robots-failure.example/robots.txt", [], 0],
    ]
    assert result.stderr == ""


def test_real_robots_private_redirect_is_closed_evidence_without_second_resolution():
    program = f"""
from signal_core.crawl_http import PinnedHttpFetcher
from signal_core.crawl_urls import CrawlScopePolicy

calls = []
def resolve(host, port, timeout):
    calls.append(host)
    return [{SERVER_ADDRESS!r}]

fetcher = PinnedHttpFetcher(resolve)
policy = CrawlScopePolicy(
    schema_version=1,
    allowed_origins=("http://robots-unsafe.example",),
    user_agent="SignalBot/1.0 (+https://signal.example/bot)",
    request_timeout_seconds=2,
    total_timeout_seconds=5,
)
result = fetcher.fetch_robots("http://robots-unsafe.example", policy=policy)
assert result.outcome == "policy_rejected"
assert result.http_status == 302
assert calls == ["robots-unsafe.example"]
"""
    result = run_program(program)
    assert result.stdout == ""
    assert result.stderr == ""
