import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

const base = new URL('../../deploy/integration-test/identity/', import.meta.url);
const profile = parse(await readFile(new URL('compose.yaml', base), 'utf8'));
const realm = JSON.parse(await readFile(new URL('realm.json', base), 'utf8'));
const dockerfile = await readFile(new URL('Dockerfile', base), 'utf8');

test('persistent identity pins its providers and keeps database TLS private', () => {
  assert.match(profile.services.database.image, /postgres:17\.11-alpine@sha256:[a-f0-9]{64}$/);
  assert.match(dockerfile, /keycloak:26\.7\.3@sha256:[a-f0-9]{64}/);
  assert.match(profile.services.identity.image, /SIGNAL_IDENTITY_IMAGE:\?/);
  assert.ok(profile.services.database.volumes.includes('database:/var/lib/postgresql/data'));
  assert.equal(profile.services.database.ports, undefined);
  assert.equal(profile.networks.database.internal, true);
  assert.match(profile.services.identity.environment.KC_DB_URL, /sslmode=verify-full&sslrootcert=/);
});

test('only private TLS identity ingress is published and outbound masquerading is disabled', () => {
  assert.deepEqual(profile.services.identity.ports, ['127.0.0.1:8280:8443']);
  assert.equal(profile.services.identity.environment.KC_HTTP_ENABLED, 'false');
  assert.equal(profile.services.identity.environment.KC_METRICS_ENABLED, 'false');
  assert.equal(profile.networks.identity.driver_opts['com.docker.network.bridge.enable_ip_masquerade'], 'false');
  assert.equal(profile.networks.identity.driver_opts['com.docker.network.bridge.name'], 'sig-identity');
});

test('identity and its database are nonroot, resource bounded and cannot dump credentials', () => {
  for (const [name, uid, memory] of [['identity', '1000:1000', '1024m'], ['database', '70:70', '512m']]) {
    const service = profile.services[name];
    assert.equal(service.user, uid);
    assert.equal(service.read_only, true);
    assert.deepEqual(service.cap_drop, ['ALL']);
    assert.deepEqual(service.security_opt, ['no-new-privileges:true']);
    assert.equal(service.mem_limit, memory);
    assert.equal(service.memswap_limit, memory);
    assert.equal(service.ulimits.core, 0);
    for (const mount of service.volumes.filter((value) => typeof value === 'object')) {
      assert.equal(mount.type, 'bind');
      assert.equal(mount.read_only, true);
      assert.equal(mount.bind.create_host_path, false);
      assert.ok(mount.source.startsWith('./') || mount.source.startsWith('/run/signal-identity/'));
    }
  }
});

test('real Google broker is owner allowlisted and cannot persist upstream tokens', () => {
  assert.equal(realm.registrationAllowed, false);
  assert.equal(realm.users, undefined);
  assert.equal(realm.identityProviders.length, 1);
  const provider = realm.identityProviders[0];
  assert.equal(provider.config.clientSecret, '${vault.google}');
  assert.equal(provider.storeToken, false);
  assert.equal(provider.config.disableNonce, 'false');
  assert.equal(provider.config.validateSignature, 'true');
  assert.equal(provider.config.pkceMethod, 'S256');
  assert.equal(provider.config.claimFilterValue, '__OWNER_EMAIL_REGEX__');
  assert.equal(provider.config.clientId, '__GOOGLE_LOGIN_CLIENT_ID__');
});

test('public client admits only exact callback and authorization-code PKCE', () => {
  assert.equal(realm.clients.length, 1);
  const client = realm.clients[0];
  assert.deepEqual(client.redirectUris, ['__SIGNAL_ORIGIN__/auth/callback']);
  assert.deepEqual(client.webOrigins, []);
  assert.equal(client.attributes['pkce.code.challenge.method'], 'S256');
  assert.equal(client.directAccessGrantsEnabled, false);
  assert.equal(client.implicitFlowEnabled, false);
  assert.equal(client.serviceAccountsEnabled, false);
});

test('identity identifiers require protected runtime configuration rather than image defaults', () => {
  assert.match(profile.services.identity.environment.KC_HOSTNAME, /SIGNAL_IDENTITY_HOSTNAME:\?/);
  assert.ok(profile.services.identity.volumes.some(mount => typeof mount === 'object' &&
    mount.source === '/run/signal-identity/keycloak/realm.json' && mount.read_only &&
    mount.bind.create_host_path === false));
});
