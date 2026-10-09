"""The only browser entry point: bounded JSON actions, no model or credentials."""

import asyncio
import base64
import http.client
import json
import sys
import time

from signal_core.browser_policy import (
    BrowserLimits,
    BrowserRejected,
    admit_browser_url,
    digest,
    reduce_links,
    validate_action,
)
from signal_core.crawl_urls import CrawlScopePolicy

STARTUP_SECONDS = 60


class ReadOnlyBrowser:
    def __init__(self, config: dict) -> None:
        if set(config) != {"origins", "user_agent", "limits", "proxy_host"}:
            raise BrowserRejected("BROWSER_CONFIG_INVALID")
        if config["proxy_host"] != "signal-egress":
            raise BrowserRejected("BROWSER_PROXY_INVALID")
        self.policy = CrawlScopePolicy(1, tuple(config["origins"]), config["user_agent"])
        for origin in self.policy.allowed_origins:
            admit_browser_url(self.policy, origin)
        self.limits = BrowserLimits(**config["limits"])
        self.deadline = time.monotonic() + self.limits.seconds
        self.steps = 0
        self.bytes = 0
        self.elements = []
        self.navigation = None
        self.failure = None
        self.blocked_path = False

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BrowserRejected("BROWSER_TIME_EXHAUSTED")
        return remaining

    async def start(self, playwright) -> None:
        self.browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": "http://signal-egress:8080", "bypass": "<-loopback>"},
            args=[
                "--disable-quic",
                "--disable-background-networking",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        self.context = await self.browser.new_context(
            accept_downloads=False,
            service_workers="block",
            user_agent=self.policy.user_agent,
            viewport={"width": 1280, "height": 720},
            permissions=[],
        )
        await self.context.route("**/*", self.route)
        await self.context.route_web_socket("**/*", lambda route: route.close())
        self.page = await self.context.new_page()
        self.context.on("page", self.new_page)
        self.page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self.page.on("download", lambda download: asyncio.create_task(download.cancel()))
        self.cdp = await self.context.new_cdp_session(self.page)
        await self.cdp.send("Browser.setDownloadBehavior", {"behavior": "deny"})
        # Defense in depth, not the action boundary: network interception independently
        # denies every unsolicited document navigation, popup, frame and non-read verb.
        await self.context.add_init_script("""(() => {
          const denied = () => { throw new Error('Signal read-only browser'); };
          for (const name of ['submit', 'requestSubmit'])
            Object.defineProperty(HTMLFormElement.prototype, name,
              {value: denied, writable: false, configurable: false});
          Object.defineProperty(window, 'open',
            {value: () => null, writable: false, configurable: false});
          document.addEventListener('submit', e => {
            e.preventDefault(); e.stopImmediatePropagation();
          }, true);
        })();""")

    async def new_page(self, page) -> None:
        if page != self.page:
            await page.close()

    async def route(self, route) -> None:
        request = route.request
        try:
            self.remaining()
            url = admit_browser_url(self.policy, request.url)
            if request.method not in {"GET", "HEAD"} or request.post_data_buffer:
                raise BrowserRejected("BROWSER_METHOD_DENIED")
            if request.is_navigation_request():
                if request.frame != self.page.main_frame or url != self.navigation:
                    raise BrowserRejected("BROWSER_UNSOLICITED_NAVIGATION_DENIED")
                self.navigation = None
            elif request.resource_type not in {
                "script",
                "stylesheet",
                "image",
                "font",
                "xhr",
                "fetch",
            }:
                raise BrowserRejected("BROWSER_RESOURCE_DENIED")
            status, media, body = await asyncio.to_thread(self.fetch, request.method, url)
            if not 200 <= status < 300:
                raise BrowserRejected("BROWSER_EGRESS_DENIED")
            self.bytes += len(body)
            if self.bytes > self.limits.bytes:
                raise BrowserRejected("BROWSER_BYTES_EXHAUSTED")
            await route.fulfill(status=status, content_type=media, body=body)
        except (BrowserRejected, OSError, http.client.HTTPException):
            self.failure = "BROWSER_NETWORK_INCOMPLETE"
            await route.abort("blockedbyclient")

    def fetch(self, method: str, url: str) -> tuple[int, str, bytes]:
        remaining = self.remaining()
        connection = http.client.HTTPConnection("signal-egress", 8080, timeout=min(5, remaining))
        try:
            connection.request(method, url)
            response = connection.getresponse()
            body = response.read(min(self.limits.bytes - self.bytes + 1, 5 * 1024 * 1024 + 1))
            return response.status, response.getheader("Content-Type", "text/plain"), body
        finally:
            connection.close()

    async def snapshot(self) -> dict:
        nodes = (await self.cdp.send("Accessibility.getFullAXTree"))["nodes"]
        links = []
        for node in nodes:
            if node.get("ignored") or node.get("role", {}).get("value") != "link":
                continue
            backend = node.get("backendDOMNodeId")
            if backend is None:
                continue
            attributes = (
                await self.cdp.send("DOM.describeNode", {"backendNodeId": backend, "depth": 0})
            )["node"].get("attributes", [])
            href = dict(zip(attributes[::2], attributes[1::2], strict=True)).get("href")
            if href is not None:
                links.append(
                    {"role": "link", "text": node.get("name", {}).get("value", ""), "url": href}
                )
            if len(links) == 1024:
                break
        self.elements = reduce_links(links, self.page.url, self.policy)
        text_info = await self.page.evaluate("""() => {
          const text = document.body?.innerText || '';
          return {text: text.slice(0, 32768), truncated: text.length > 32768};
        }""")
        self.blocked_path = await self.page.evaluate("""() => Boolean(
          document.querySelector('form,input,textarea,[role="textbox"],[class*="captcha"],iframe')
        )""")
        return {
            "url": self.page.url,
            "text": text_info["text"],
            "text_truncated": text_info["truncated"],
            "elements": self.elements,
            "element_digest": digest(self.elements),
            "blocked_path": self.blocked_path,
            "network_incomplete": self.failure is not None,
        }

    async def execute(self, raw: object) -> dict:
        action = validate_action(raw)
        self.remaining()
        if self.steps >= self.limits.steps:
            raise BrowserRejected("BROWSER_STEPS_EXHAUSTED")
        self.steps += 1
        kind = action["action"]
        if kind in {"navigate", "follow_link"}:
            if kind == "follow_link":
                if self.blocked_path or self.failure:
                    raise BrowserRejected("BROWSER_PATH_STOPPED")
                matches = [e for e in self.elements if e["id"] == action["id"]]
                if len(matches) != 1:
                    raise BrowserRejected("BROWSER_ELEMENT_DENIED")
                url = matches[0]["url"]
            else:
                url = action["url"]
            self.navigation = admit_browser_url(self.policy, url)
            self.failure = None
            await self.page.goto(
                self.navigation,
                wait_until="domcontentloaded",
                timeout=min(10000, self.remaining() * 1000),
            )
        elif kind == "scroll":
            await self.page.evaluate("pixels => window.scrollBy(0, pixels)", action["pixels"])
        elif kind == "wait_network_idle":
            await self.page.wait_for_load_state(
                "networkidle", timeout=min(action["milliseconds"], self.remaining() * 1000)
            )
        snapshot = await self.snapshot()
        result = {"snapshot": snapshot, "snapshot_digest": digest(snapshot), "bytes": self.bytes}
        if kind == "screenshot":
            picture = await self.page.screenshot(
                type="png", timeout=min(5000, self.remaining() * 1000)
            )
            self.bytes += len(picture)
            if self.bytes > self.limits.bytes:
                raise BrowserRejected("BROWSER_BYTES_EXHAUSTED")
            result["screenshot"] = base64.b64encode(picture).decode("ascii")
            result["bytes"] = self.bytes
        self.remaining()
        return result


async def main() -> None:
    from playwright.async_api import async_playwright

    config = json.loads(sys.stdin.buffer.readline(65537))
    worker = ReadOnlyBrowser(config)
    async with async_playwright() as playwright:
        reader = asyncio.StreamReader(limit=65536)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
        )
        try:
            await asyncio.wait_for(worker.start(playwright), timeout=STARTUP_SECONDS)
            worker.deadline = time.monotonic() + worker.limits.seconds
            print(json.dumps({"ready": True}), flush=True)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=worker.remaining())
                if not line:
                    break
                try:
                    result = await asyncio.wait_for(
                        worker.execute(json.loads(line)), timeout=worker.remaining()
                    )
                except BrowserRejected as error:
                    result = {"error": str(error)}
                except Exception:
                    result = {"error": "BROWSER_EXECUTION_INCOMPLETE"}
                print(json.dumps(result, ensure_ascii=True), flush=True)
                if "error" in result:
                    break
        finally:
            transport.close()
            if hasattr(worker, "browser"):
                await worker.browser.close()


if __name__ == "__main__":
    asyncio.run(main())
