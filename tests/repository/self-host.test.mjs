import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

const base = new URL('../../deploy/self-host/', import.meta.url);
const compose = parse(await readFile(new URL('compose.yaml', base), 'utf8'), { merge: true });

test('self-host publishes only HTTPS and leaves every administrative surface private', () => {
  assert.equal(Object.keys(compose.services).length, 12);
  for (const [name, service] of Object.entries(compose.services)) {
    assert.equal(service.privileged, undefined);
    assert.equal(service.read_only, true);
    assert.notEqual(service.user.split(':')[0], '0');
    assert.deepEqual(service.cap_drop, ['ALL']);
    assert.deepEqual(service.security_opt, ['no-new-privileges:true']);
    if (name === 'ingress') assert.deepEqual(service.ports, ['443:8443']);
    else assert.equal(service.ports, undefined);
    assert.match(service.image, /@sha256:[a-f0-9]{64}$|^\$\{SIGNAL_[A-Z]+_IMAGE:\?/);
    assert.ok(!service.networks.includes('edge') || name === 'ingress');
  }
  for (const [name, network] of Object.entries(compose.networks)) {
    if (name !== 'edge') assert.equal(network.internal, true);
  }
});

test('self-host Caddy administration and wildcard provider ingress are denied', async () => {
  const source = await readFile(new URL('Caddyfile', base), 'utf8');
  assert.match(source, /admin off/);
  assert.match(source, /auto_https off/);
  assert.match(source, /output discard/);
  assert.match(source, /__PROVIDER_CALLBACKS__/);
  assert.match(source, /@private path .*\/identity\* .*\/admin\*/);
  assert.doesNotMatch(source, /tls_insecure_skip_verify|broker\/\*|127\.0\.0\.1:8000/);
});

test('self-host operator documentation is explicit about certification and recovery', async () => {
  const source = await readFile(new URL('../../docs/self-host.md', import.meta.url), 'utf8');
  for (const term of ['NOT_EXECUTED', 'NOT_CERTIFIED', 'Shamir', 'complete-owner',
    'backup', 'upgrade', 'signal-self-host', 'DataForSEO', 'INV-032']) assert.ok(source.includes(term));
  assert.match(source, /signal-self-host-tls/);
  assert.doesNotMatch(source, /docker compose down -v|start-dev|tls_insecure_skip_verify/);
});
