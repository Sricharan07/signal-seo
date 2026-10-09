import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const moduleSource = await readFile(
  new URL("../../services/control_plane/src/signal_core/gsc_properties.py", import.meta.url),
  "utf8",
);
const providerCheck = await readFile(
  new URL("../../scripts/check-gsc-provider-boundary.py", import.meta.url),
  "utf8",
);
const project = await readFile(new URL("../../pyproject.toml", import.meta.url), "utf8");

test("GSC discovery is fixed-origin, read-only, bounded, and included in tests", () => {
  assert.match(
    moduleSource,
    /GSC_READONLY_SCOPE = "https:\/\/www\.googleapis\.com\/auth\/webmasters\.readonly"/,
  );
  assert.doesNotMatch(
    moduleSource,
    /GSC_READONLY_SCOPE = "https:\/\/www\.googleapis\.com\/auth\/webmasters"/,
  );
  assert.match(
    moduleSource,
    /GSC_SITES_URL = "https:\/\/www\.googleapis\.com\/webmasters\/v3\/sites"/,
  );
  assert.match(moduleSource, /follow_redirects=False/);
  assert.match(moduleSource, /trust_env=False/);
  assert.match(moduleSource, /_MAX_RESPONSE_BYTES = 128 \* 1024/);
  assert.match(providerCheck, /signal-gsc-negative-provider-check/);
  assert.match(project, /"tests\/connectors"/);
});
