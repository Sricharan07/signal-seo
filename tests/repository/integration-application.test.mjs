import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { mkdtempSync, writeFileSync, chmodSync, symlinkSync, linkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { parse } from 'yaml';
import { dashboardServerOptions, readDashboardOrigin } from '../../deploy/integration-test/application/dashboard-server-options.mjs';

const base = new URL('../../deploy/integration-test/application/', import.meta.url);
const profile = parse(await readFile(new URL('compose.yaml', base), 'utf8'), { merge: true });

test('dashboard origin requires canonical bounded private configuration, not environment trust', () => {
  const directory = mkdtempSync(join(tmpdir(), 'signal-origin-test-'));
  const path = join(directory, 'origin.json');
  const origin = 'https://signal-test.example.invalid';
  const canonical = JSON.stringify({ origin });
  try {
    writeFileSync(path, canonical, { mode: 0o600 });
    assert.equal(readDashboardOrigin(path), origin);
    chmodSync(path, 0o644);
    assert.throws(() => readDashboardOrigin(path));
    chmodSync(path, 0o600);
    for (const content of ['{}', 'null', 'not-json', 'x'.repeat(4097),
      JSON.stringify({ origin, extra: true }),
      `{"origin":"${origin}","origin":"${origin}"}`,
      JSON.stringify({ origin: 'https://127.0.0.1' }),
      JSON.stringify({ origin: 'http://signal-test.example.invalid' })]) {
      writeFileSync(path, content);
      assert.throws(() => readDashboardOrigin(path));
    }
    writeFileSync(path, canonical);
    symlinkSync(path, join(directory, 'symlink'));
    assert.throws(() => readDashboardOrigin(join(directory, 'symlink')));
    linkSync(path, join(directory, 'hardlink'));
    assert.throws(() => readDashboardOrigin(path));
    assert.throws(() => readDashboardOrigin(join(directory, 'missing')));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('test login profile is persistent, private and bounded', () => {
  const database = profile.services['application-database'];
  assert.equal(database.ports, undefined);
  assert.equal(profile.networks.database.internal, true);
  assert.equal(profile.networks.api.internal, true);
  assert.equal(profile.networks.ingress.driver_opts['com.docker.network.bridge.enable_ip_masquerade'], 'false');
  assert.deepEqual(profile.services.api.ports, ['127.0.0.1:8380:8443']);
  assert.deepEqual(profile.services.dashboard.ports, ['127.0.0.1:8480:8443']);
  for (const service of Object.values(profile.services)) {
    assert.equal(service.read_only, true);
    assert.deepEqual(service.cap_drop, ['ALL']);
    assert.deepEqual(service.security_opt, ['no-new-privileges:true']);
    assert.equal(service.mem_limit, '512m');
    assert.equal(service.memswap_limit, '512m');
    assert.equal(service.ulimits.core, 0);
    assert.ok(service.volumes.every(value => !value.includes('/:/')));
  }
});

test('dashboard uses real HTTPS BFF and no pilot cookie downgrade', () => {
  assert.deepEqual(profile.services.dashboard.environment, {
    SIGNAL_API_BASE_URL: 'https://api:8443',
    SIGNAL_DASHBOARD_ORIGIN: '${SIGNAL_DASHBOARD_ORIGIN:?Protected origin configuration required}',
    SIGNAL_IDENTITY_PROVIDER_ORIGIN: '${SIGNAL_IDENTITY_PROVIDER_ORIGIN:?Protected identity origin required}',
    SIGNAL_OWNER_CONNECTOR_SCOPE: '${SIGNAL_OWNER_CONNECTOR_SCOPE:?Protected repository configuration required}',
  });
});

test('Next callback metadata uses the exact public origin, not its private TLS listener', async () => {
  assert.deepEqual(dashboardServerOptions('https://signal-test.example.invalid', 'https://signal-test.example.invalid'), {
    dev: false, hostname: 'signal-test.example.invalid', port: 443,
  });
  const source = await readFile(new URL('dashboard-server.mjs', base), 'utf8');
  assert.match(source, /next\(dashboardServerOptions\(process.env.SIGNAL_DASHBOARD_ORIGIN, configuredOrigin\)\)/);
  assert.match(source, /readDashboardOrigin\("\/run\/signal-config\/dashboard-origin.json"\)/);
  assert.match(source, /server.listen\(8443, "0.0.0.0"\)/);
  const dockerfile = await readFile(new URL('Dockerfile.dashboard', base), 'utf8');
  assert.match(dockerfile, /COPY deploy\/integration-test\/application\/dashboard-server-options.mjs/);
  const context = await readFile(new URL('Dockerfile.dashboard.dockerignore', base), 'utf8');
  assert.match(context, /!deploy\/integration-test\/application\/dashboard-server-options.mjs/);
});

test('callback metadata configuration rejects absent, private or broadened origins', () => {
  for (const origin of [undefined, '', 'https://dashboard:8443', 'http://signal-test.example.invalid',
    'https://signal-test.example.invalid:8443', 'https://attacker.invalid',
    'https://signal-test.example.invalid/path', 'https://signal-test.example.invalid?other=1']) {
    assert.throws(() => dashboardServerOptions(origin, 'https://signal-test.example.invalid'), /Exact dedicated dashboard origin required/);
  }
});

test('owner robots use an API-only named artifact volume, never a host or crawler root', () => {
  assert.ok(profile.services.api.volumes.includes('owner-robots:/var/lib/signal-owner-robots'));
  assert.ok(Object.hasOwn(profile.volumes, 'owner-robots'));
  for (const [name, service] of Object.entries(profile.services)) {
    if (name !== 'api') assert.ok(service.volumes.every(value => !value.startsWith('owner-robots:')));
  }
});

test('API image has no startup migration or disposable identity composition', async () => {
  const source = await readFile(new URL('Dockerfile.api', base), 'utf8');
  assert.match(source, /python:3\.12\.14-slim-bookworm@sha256:[a-f0-9]{64}/);
  assert.match(source, /--no-access-log/);
  assert.match(source, /--no-proxy-headers/);
  assert.doesNotMatch(source, /local_pilot|start-dev|alembic.*upgrade/);
});

test('public ingress preserves form Origin while suppressing credential URL referrers', async () => {
  const source = await readFile(new URL('Caddyfile', base), 'utf8');
  assert.match(source, /@credential_url path \/identity\/\* \/auth\/callback \/auth\/slack\/callback \/auth\/gsc\/callback/);
  assert.match(source, /header @credential_url \{\s+Referrer-Policy no-referrer\s+defer/);
  assert.match(source, /@dashboard_url not path \/identity\/\* \/auth\/callback \/auth\/slack\/callback \/auth\/gsc\/callback/);
  assert.match(source, /header @dashboard_url \{\s+Referrer-Policy same-origin\s+defer/);
  assert.match(source, /admin off/);
  assert.match(source, /output discard/);
  assert.match(source, /request delete\s+resp_headers delete/);
  assert.match(source, /handle_errors \{\s+header Content-Type "text\/html; charset=utf-8"/);
  assert.doesNotMatch(source, /log_credentials/);
  assert.doesNotMatch(source, /tls_insecure_skip_verify|localhost:8000|local_pilot/);
});

test('broker completion routes are exact and do not expose other providers', async () => {
  const source = await readFile(new URL('Caddyfile', base), 'utf8');
  const paths = source.match(/@identity path (.+)/)[1].split(' ');
  assert.ok(paths.includes('/identity/realms/signal/broker/after-first-broker-login'));
  assert.ok(paths.includes('/identity/realms/signal/broker/after-post-broker-login'));
  assert.ok(!paths.includes('/identity/realms/signal/broker/*'));
  assert.ok(!paths.includes('/identity/realms/*'));
  assert.ok(!paths.some(path => path.includes('/admin')));
});
