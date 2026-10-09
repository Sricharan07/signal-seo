"""Check Ask Signal end to end on a running local pilot (`npm run pilot`).

Drives the dashboard exactly as a browser would: OIDC sign-in with the pilot's
local-only account, organization and site selection, then every Ask Signal
route through the dashboard BFF to the API and PostgreSQL. Loopback only.

Usage: .venv/bin/python scripts/ask_signal_pilot_check.py [http://localhost:3000]
"""

import html
import json
import re
import sys
import uuid
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlparse

import httpx2
from local_pilot import PILOT_PASSWORD, PILOT_USERNAME

ORIGIN = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
if urlparse(ORIGIN).hostname not in {"localhost", "127.0.0.1"}:
    raise SystemExit("The pilot check runs only against a loopback dashboard.")
SAME = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
FORM = {**SAME, "Content-Type": "application/x-www-form-urlencoded"}
client = httpx2.Client(follow_redirects=False, timeout=120)
# Cookies are kept by hand so the dashboard's Secure cookies also travel over loopback HTTP.
jar: dict[str, dict[str, str]] = {}
failures = 0


def send(method, url, **kwargs):
    host = urlparse(url).netloc
    headers = dict(kwargs.pop("headers", {}))
    if jar.get(host):
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar[host].items())
    response = client.request(method, url, headers=headers, **kwargs)
    for raw in response.headers.get_list("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(raw)
        for name, morsel in cookie.items():
            if morsel["max-age"] == "0" or morsel.value == "":
                jar.setdefault(host, {}).pop(name, None)
            else:
                jar.setdefault(host, {})[name] = morsel.value
    return response


def follow(response):
    for _ in range(10):
        if response.status_code not in {301, 302, 303, 307, 308}:
            break
        response = send("GET", urljoin(str(response.url), response.headers["location"]))
    return response


def check(name, ok, detail=""):
    global failures
    failures += not ok
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)


def fields(page, action):
    form = re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>', page, re.S)
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(1)))


def home():
    return send("GET", ORIGIN + "/").text


page = follow(send("POST", ORIGIN + "/auth/login", headers=FORM, content=b"")).text
login = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page)
follow(
    send(
        "POST",
        html.unescape(login.group(1)),
        data={"username": PILOT_USERNAME, "password": PILOT_PASSWORD, "credentialId": ""},
    )
)
page = home()
check("signed in", "Sign in to Signal" not in page)
if 'action="/auth/select-organization"' in page:
    follow(
        send(
            "POST",
            ORIGIN + "/auth/select-organization",
            headers=FORM,
            data=fields(page, "/auth/select-organization"),
        )
    )
    page = home()
if 'action="/auth/create-site"' in page and 'action="/auth/select-site"' not in page:
    values = fields(page, "/auth/create-site")
    values.update(
        name="Pilot docs",
        primary_origin="https://docs.example.test",
        timezone="UTC",
        reporting_currency="USD",
    )
    follow(send("POST", ORIGIN + "/auth/create-site", headers=FORM, data=values))
    page = home()
site = None
if 'action="/auth/select-site"' in page:
    values = fields(page, "/auth/select-site")
    follow(send("POST", ORIGIN + "/auth/select-site", headers=FORM, data=values))
    site = values["site_id"]
else:
    # A newly created site is already current; read the site the Ask Signal button is bound to.
    active = re.search(r'siteId\\?":\\?"([0-9a-f-]{36})', page)
    site = active.group(1) if active else None
check("site selected", site is not None)


def read(resource, **extra):
    query = "&".join(
        f"{k}={v}" for k, v in {"site_id": site, "resource": resource, **extra}.items()
    )
    response = send("GET", f"{ORIGIN}/actions/assistant?{query}")
    return response.status_code, response.json()


def command(body, headers=SAME):
    payload = json.dumps({"schema_version": 1, "site_id": site, **body}).encode()
    response = send(
        "POST",
        ORIGIN + "/actions/assistant",
        headers={**headers, "Content-Type": "application/json"},
        content=payload,
    )
    return response.status_code, response.json()


status, overview = read("overview")
check("overview", status == 200, f"availability={overview.get('availability')}")
start = str(uuid.uuid4())
status, created = command({"action": "start", "request_id": start})
check("conversation created", status == 201)
conversation = created.get("conversation_id")
check("start is idempotent", command({"action": "start", "request_id": start})[1] == created)
question = {
    "action": "send",
    "conversation_id": conversation,
    "request_id": str(uuid.uuid4()),
    "text": "What is waiting on me this week?",
}
status, reply = command(question)
signal = reply.get("reply", {})
check(
    "question answered or honestly refused",
    status == 200 and signal.get("role") == "signal",
    f"state={signal.get('state')}",
)
check("replay returns the same reply", command(question)[1].get("reply") == signal)
status, thread = read("conversation", conversation_id=conversation)
check("conversation persists", status == 200 and len(thread["messages"]) == 2)
status, memory = command(
    {
        "action": "remember",
        "request_id": str(uuid.uuid4()),
        "kind": "preference",
        "text": "I prefer short answers with numbers.",
    }
)
check("memory added", status == 201)
status, _ = command(
    {
        "action": "remember",
        "request_id": str(uuid.uuid4()),
        "kind": "context",
        "text": "We sell docs hosting to startups.",
    }
)
check("business statement is not memory", status == 409)
listed = [item["memory_id"] for item in read("memory")[1]["memories"]]
check("memory listed", memory.get("memory_id") in listed)
check(
    "memory forgotten",
    command({"action": "forget", "memory_id": memory.get("memory_id")})[1].get("state")
    == "forgotten",
)
listed = [item["memory_id"] for item in read("memory")[1]["memories"]]
check("forgotten memory is gone", memory.get("memory_id") not in listed)
check(
    "conversation listed first",
    read("overview")[1]["conversations"][0]["conversation_id"] == conversation,
)
page = send("GET", f"{ORIGIN}/chat?conversation={conversation}").text
check(
    "chat page shows the stored conversation",
    "What is waiting on me this week?" in page and "What Signal remembers" in page,
)
check("top bar offers Ask Signal", "Ask Signal about your site" in page)
status, _ = command(
    {"action": "forget", "memory_id": str(uuid.uuid4())},
    headers={"Origin": "https://cross-site.invalid", "Sec-Fetch-Site": "cross-site"},
)
check("cross-site command rejected", status == 403)
sys.exit(1 if failures else 0)
