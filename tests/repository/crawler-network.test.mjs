import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const dockerfileUrl = new URL('../../deploy/crawler-network/Dockerfile', import.meta.url);
const labUrl = new URL('../../scripts/crawler_network_lab.py', import.meta.url);
const ignoreUrl = new URL('../../.dockerignore', import.meta.url);

test('crawler network image is digest-pinned, minimal, and non-root', async () => {
  const dockerfile = await readFile(dockerfileUrl, 'utf8');
  assert.match(dockerfile, /^FROM python:3\.12\.14-slim-bookworm@sha256:[a-f0-9]{64}$/m);
  assert.match(dockerfile, /^USER 10001:10001$/m);
  assert.match(dockerfile, /^ENTRYPOINT \["python"\]$/m);
  assert.doesNotMatch(dockerfile, /apt-get|apk add|curl|wget|pip install|COPY \. /);
  assert.match(dockerfile, /COPY --chown=10001:10001 .*crawl_urls\.py/);
  assert.match(dockerfile, /COPY --chown=10001:10001 .*crawl_http\.py/);

  const ignored = await readFile(ignoreUrl, 'utf8');
  assert.match(ignored, /^\*\*$/m);
  assert.match(ignored, /^!deploy\/crawler-network\/Dockerfile$/m);
  assert.match(ignored, /^!deploy\/crawler-network\/server\.py$/m);
});

test('crawler network lab has no host publication or broad mount and owns exact cleanup', async () => {
  const source = await readFile(labUrl, 'utf8');
  const allocator = await readFile(new URL('../../scripts/lab_network.py', import.meta.url), 'utf8');
  assert.match(source, /create_public_network\(network, f"\{LABEL\}=\{run_id\}"\)/);
  assert.match(allocator, /"--internal"/);
  assert.match(allocator, /"com\.docker\.network\.bridge\.enable_ip_masquerade=false"/);
  assert.match(allocator, /"com\.docker\.network\.bridge\.gateway_mode_ipv4=isolated"/);
  assert.match(allocator, /validate_public_addresses/);
  assert.doesNotMatch(source, /93\.184\.216/);
  assert.match(source, /"--read-only"/);
  assert.match(source, /"--cap-drop"/);
  assert.match(source, /"no-new-privileges:true"/);
  assert.match(source, /f"label=\{LABEL\}=\{run_id\}"/);
  assert.doesNotMatch(source, /"--publish"|"--volume"|"--privileged"|"--network",\s*"host"/);
});
