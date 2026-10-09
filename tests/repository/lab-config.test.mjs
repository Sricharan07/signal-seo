import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

test('WordPress lab is digest-pinned, isolated, bounded, and does not publish ports', async () => {
  const configuration = parse(await readFile(new URL('../../experiments/wordpress-feasibility/compose.yaml', import.meta.url), 'utf8'));
  assert.equal(configuration.networks.lab.internal, true);
  for (const service of Object.values(configuration.services)) {
    assert.match(service.image, /@sha256:[a-f0-9]{64}$/);
    assert.deepEqual(service.networks, ['lab']);
    assert.equal(service.ports, undefined);
    assert.equal(service.privileged, undefined);
    assert.equal(service.network_mode, undefined);
    assert.equal(service.restart, undefined);
    assert.ok(service.mem_limit);
    assert.ok(service.cpus);
    assert.deepEqual(service.security_opt, ['no-new-privileges:true']);
    for (const volume of service.volumes ?? []) {
      if (typeof volume === 'object') {
        assert.equal(volume.type, 'bind');
        assert.match(volume.source, /^\$\{SIGNAL_LAB_SOURCE:\?/);
        assert.equal(volume.target, '/lab');
        assert.equal(volume.read_only, true);
      } else assert.equal(volume, 'wordpress:/var/www/html');
    }
  }
  assert.deepEqual(configuration.services.database.tmpfs, ['/var/lib/mysql']);
  assert.match(configuration.services.database.environment.MARIADB_ROOT_PASSWORD, /\$\{SIGNAL_LAB_ROOT_PASSWORD:\?/);
});

test('Keycloak lab is synthetic, digest-pinned, TLS-only, loopback, and bounded', async () => {
  const runner = await readFile(new URL('../../scripts/keycloak_lab.py', import.meta.url), 'utf8');
  const realm = JSON.parse(await readFile(new URL('../../experiments/keycloak-feasibility/realm.json', import.meta.url), 'utf8'));
  assert.match(runner, /quay\.io\/keycloak\/keycloak:26\.7\.3@"\s*"sha256:[a-f0-9]{64}/);
  assert.match(runner, /"127\.0\.0\.1::8443"/);
  assert.match(runner, /"--memory",\s*"768m"/);
  assert.match(runner, /"--cpus",\s*"1"/);
  assert.match(runner, /"--pids-limit",\s*"256"/);
  assert.match(runner, /"no-new-privileges:true"/);
  assert.match(runner, /"--user",\s*container_user/);
  assert.match(runner, /return f"\{uid\}:0"/);
  assert.match(runner, /"--http-enabled=false"/);
  assert.doesNotMatch(runner, /verify\s*=\s*False/);
  assert.doesNotMatch(runner, /"--privileged"/);
  assert.equal(realm.realm, 'signal-lab');
  assert.equal(realm.registrationAllowed, false);
  assert.equal(realm.clients[0].publicClient, true);
  assert.equal(realm.clients[0].implicitFlowEnabled, false);
  assert.equal(realm.clients[0].directAccessGrantsEnabled, false);
  assert.deepEqual(realm.clients[0].redirectUris, ['https://127.0.0.1/callback']);
  assert.equal(realm.clients[0].attributes['pkce.code.challenge.method'], 'S256');
  assert.match(realm.users[0].email, /\.invalid$/);
  assert.match(realm.users[0].credentials[0].value, /^signal-lab-only-/);
});

test('OpenBao lab is synthetic, digest-pinned, TLS-only, loopback, and bounded', async () => {
  const runner = await readFile(new URL('../../scripts/openbao_lab.py', import.meta.url), 'utf8');
  assert.match(runner, /ghcr\.io\/openbao\/openbao:2\.6\.1@"\s*"sha256:[a-f0-9]{64}/);
  assert.match(runner, /"127\.0\.0\.1::8200"/);
  assert.match(runner, /"--memory",\s*"384m"/);
  assert.match(runner, /"--cpus",\s*"1"/);
  assert.match(runner, /"--pids-limit",\s*"128"/);
  assert.match(runner, /"no-new-privileges:true"/);
  assert.match(runner, /"-dev-tls"/);
  assert.match(runner, /\/tmp\/bao-tls:rw,noexec,nosuid,nodev,size=2m/);
  assert.match(runner, /capabilities = \["create"\]/);
  assert.match(runner, /capabilities = \["read"\]/);
  assert.match(runner, /capabilities = \["delete"\]/);
  assert.match(runner, /AUTHORITY_MOUNT = "signal-authority"/);
  assert.match(runner, /data\/recovery\/current/);
  assert.match(runner, /RECOVERY_POLICY = "signal-recovery-reader"/);
  assert.match(runner, /"options": \{"cas": 0\}/);
  assert.match(runner, /recovery authority rejects reader mutation/);
  assert.match(runner, /root_token = "root-" \+ secrets\.token_urlsafe\(32\)/);
  assert.match(runner, /env=dict\(os\.environ, BAO_DEV_ROOT_TOKEN_ID=root_token\)/);
  assert.doesNotMatch(runner, /verify\s*=\s*False/);
  assert.doesNotMatch(runner, /"--privileged"/);
  assert.doesNotMatch(runner, /^\s*root_token\s*=\s*["'][^"']+["']\s*$/m);
});
