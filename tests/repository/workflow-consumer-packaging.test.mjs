import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

const composeUrl = new URL('../../deploy/workflow-consumer/compose.yaml', import.meta.url);
const dockerfileUrl = new URL('../../deploy/workflow-consumer/Dockerfile', import.meta.url);
const lockUrl = new URL('../../deploy/workflow-consumer/requirements.txt', import.meta.url);
const rootLockUrl = new URL('../../requirements.txt', import.meta.url);

test('workflow consumer image is digest-pinned, non-root, health-checked, and release-bound', async () => {
  const dockerfile = await readFile(dockerfileUrl, 'utf8');
  assert.match(dockerfile, /^FROM python:3\.12\.14-slim-bookworm@sha256:[a-f0-9]{64}$/m);
  assert.match(dockerfile, /re\.fullmatch\(r'sha256:\[a-f0-9\]\{64\}'/);
  assert.match(dockerfile, /^USER 10001:10001$/m);
  assert.match(dockerfile, /^STOPSIGNAL SIGTERM$/m);
  assert.match(dockerfile, /^HEALTHCHECK .*--retries=3/m);
  assert.match(dockerfile, /CMD \["python", "\/opt\/signal\/check-workflow-consumer-health\.py"\]/);
  assert.match(dockerfile, /^ENTRYPOINT \["python", "\/opt\/signal\/run-workflow-consumer\.py"\]$/m);
  assert.doesNotMatch(dockerfile, /apt-get|apk add|curl|wget|COPY \. /);

  const requirements = (await readFile(lockUrl, 'utf8'))
    .split('\n')
    .filter((line) => line && !line.startsWith('#'));
  const rootRequirements = new Set((await readFile(rootLockUrl, 'utf8')).split('\n'));
  assert.equal(requirements.length, 7);
  assert.ok(requirements.every((line) => /^[A-Za-z0-9_-]+==[^=\s]+$/.test(line)));
  assert.ok(requirements.every((line) => rootRequirements.has(line)));
  for (const forbidden of ['pytest', 'ruff', 'fastapi', 'uvicorn', 'authlib']) {
    assert.ok(!requirements.some((line) => line.toLowerCase().startsWith(`${forbidden}==`)));
  }
});

test('workflow consumer compose contract keeps authority private and bounded', async () => {
  const configuration = parse(await readFile(composeUrl, 'utf8'));
  const service = configuration.services['workflow-consumer'];

  assert.match(
    service.image,
    /^\$\{SIGNAL_WORKFLOW_CONSUMER_REPOSITORY:\?.*\}@sha256:\$\{SIGNAL_WORKFLOW_CONSUMER_DIGEST:\?.*\}$/,
  );
  assert.equal(service.build, undefined);
  assert.equal(service.ports, undefined);
  assert.equal(service.volumes, undefined);
  assert.equal(service.privileged, undefined);
  assert.equal(service.network_mode, undefined);
  assert.equal(service.user, '10001:10001');
  assert.equal(service.init, true);
  assert.equal(service.read_only, true);
  assert.deepEqual(service.cap_drop, ['ALL']);
  assert.deepEqual(service.security_opt, ['no-new-privileges:true']);
  assert.deepEqual(service.networks, ['signal-control']);
  assert.match(service.tmpfs[0], /noexec,nosuid,nodev/);
  assert.equal(service.restart, 'on-failure:5');
  assert.equal(service.stop_signal, 'SIGTERM');
  assert.equal(service.stop_grace_period, '90s');
  assert.equal(service.pids_limit, 128);
  assert.equal(service.mem_limit, '384m');
  assert.equal(service.cpus, 1);
  assert.deepEqual(configuration.networks['signal-control'].external, true);

  assert.equal(service.environment.SIGNAL_SCHEDULER_DSN, undefined);
  assert.equal(service.environment.SIGNAL_WORKFLOW_DSN, undefined);
  assert.equal(service.environment.SIGNAL_SCHEDULER_DSN_FILE, '/run/secrets/scheduler_dsn');
  assert.equal(service.environment.SIGNAL_WORKFLOW_DSN_FILE, '/run/secrets/workflow_dsn');
  assert.equal(service.environment.SIGNAL_WORKFLOW_CONSUMER_INSECURE_LOOPBACK, undefined);
  assert.equal(Object.keys(configuration.secrets).length, 6);
  assert.equal(service.secrets.length, 6);
  assert.ok(service.secrets.every((secret) => String(secret.target).startsWith(secret.source.split('_')[0]) || secret.target === 'database_root_ca'));
});
