import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const moduleSource = await readFile(
  new URL("../../services/control_plane/src/signal_core/github_app.py", import.meta.url),
  "utf8",
);
const egressSource = await readFile(
  new URL("../../services/control_plane/src/signal_core/github_read_binding.py", import.meta.url),
  "utf8",
);
const project = await readFile(new URL("../../pyproject.toml", import.meta.url), "utf8");

test("GitHub read inspection stays narrow while PR permission is separately observed", () => {
  assert.match(moduleSource, /GITHUB_API_ORIGIN = "https:\/\/api\.github\.com"/);
  assert.match(moduleSource, /GITHUB_API_VERSION = "2026-03-10"/);
  assert.match(moduleSource, /"repositories": \[selected\.repository\]/);
  assert.match(moduleSource, /_READ_PERMISSIONS = \{"contents": "read"\}/);
  assert.match(moduleSource, /_PR_PERMISSIONS = \{"contents": "read", "pull_requests": "write"\}/);
  assert.match(moduleSource, /async def inspect_github_repository\([\s\S]*?requested_permissions=_READ_PERMISSIONS/);
  assert.match(moduleSource, /async def inspect_github_pr_authority\([\s\S]*?requested_permissions=_PR_PERMISSIONS/);
  assert.match(moduleSource, /"permissions": requested_permissions/);
  assert.match(moduleSource, /set\(permissions\) - \(set\(requested_permissions\) \| \{"metadata"\}\)/);
  assert.match(moduleSource, /permissions\.get\(key\) != value for key, value in requested_permissions\.items\(\)/);
  assert.doesNotMatch(egressSource, /\/pulls|\/git\/refs|\/git\/trees\/\{.*\}.*POST/);
  assert.match(moduleSource, /follow_redirects=False/);
  assert.match(moduleSource, /trust_env=False/);
  assert.match(moduleSource, /_MAX_RESPONSE_BYTES = 256 \* 1024/);
  assert.match(moduleSource, /private_key_pem: str = field\(repr=False\)/);
  assert.doesNotMatch(moduleSource, /personal access token|Authorization: token|ghp_/i);
  assert.match(project, /"tests\/connectors"/);
});
