import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

const profile = parse(await readFile(new URL('../../deploy/integration-test/secrets/compose.yaml', import.meta.url), 'utf8'));
const store = profile.services.openbao;

test('integration secrets use persistent non-development OpenBao with an immutable image', () => {
  assert.match(store.image, /openbao:2\.6\.1@sha256:[a-f0-9]{64}$/);
  assert.deepEqual(store.entrypoint, ['bao']);
  assert.deepEqual(store.command, ['server', '-config=/openbao/config/server.hcl']);
  assert.equal(store.command.some((value) => value.startsWith('-dev')), false);
  assert.ok(store.volumes.includes('data:/openbao/file'));
  assert.ok(store.volumes.includes('audit:/openbao/logs'));
});

test('only loopback TLS is published and administration has no public route', () => {
  assert.deepEqual(store.ports, ['127.0.0.1:8200:8200']);
  assert.deepEqual(store.networks, ['secrets', 'application_access']);
  assert.equal(profile.networks.application_access.external, true);
  assert.equal(profile.networks.application_access.name, 'signal-test-secrets');
  assert.equal(profile.networks.secrets.driver_opts['com.docker.network.bridge.enable_ip_masquerade'], 'false');
  assert.equal(profile.networks.secrets.driver_opts['com.docker.network.bridge.name'], 'sig-bao');
});

test('secret store is nonroot and bounded with only narrow readonly host mounts', () => {
  assert.equal(store.user, '100:1000');
  assert.equal(store.read_only, true);
  assert.deepEqual(store.cap_drop, ['ALL']);
  assert.deepEqual(store.security_opt, ['no-new-privileges:true']);
  assert.equal(store.mem_limit, '384m');
  assert.equal(store.memswap_limit, store.mem_limit);
  assert.equal(store.pids_limit, 128);
  assert.equal(store.ulimits.core, 0);
  assert.deepEqual(store.tmpfs, ['/tmp:rw,noexec,nosuid,nodev,size=72m,mode=1777']);
  for (const mount of store.volumes.filter((value) => typeof value === 'object')) {
    assert.equal(mount.type, 'bind');
    assert.equal(mount.read_only, true);
    assert.equal(mount.bind.create_host_path, false);
    assert.ok(['./tls', './server.hcl'].includes(mount.source));
  }
});

test('health validates TLS without retaining a root or provider credential', () => {
  assert.deepEqual(store.environment, {BAO_ADDR: 'https://localhost:8200', BAO_CACERT: '/openbao/tls/ca.pem'});
  assert.deepEqual(store.healthcheck.test, ['CMD', 'bao', 'status', '-format=json']);
  assert.equal(store.logging.options['max-size'], '10m');
  assert.equal(store.logging.options['max-file'], '3');
});
