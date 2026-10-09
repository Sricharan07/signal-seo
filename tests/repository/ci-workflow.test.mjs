import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import test from 'node:test';
import { parse } from 'yaml';

const expectedActions = new Set([
  'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
  'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
  'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
]);

test('quality workflow is least-privilege, pinned, bounded, and runs every gate', async () => {
  const source = await readFile(new URL('../../.github/workflows/quality.yml', import.meta.url), 'utf8');
  const workflow = parse(source);
  assert.deepEqual(workflow.permissions, { contents: 'read' });
  assert.ok(Object.hasOwn(workflow.on, 'pull_request'));
  assert.deepEqual(workflow.on.push.branches, ['main']);
  assert.equal(workflow.on.pull_request_target, undefined);
  assert.equal(workflow.concurrency['cancel-in-progress'], true);
  assert.doesNotMatch(source, /\$\{\{\s*secrets\./);
  assert.deepEqual(workflow.jobs['autonomy-delivery'].strategy.matrix.shard, [0, 1]);
  assert.equal(workflow.jobs['autonomy-delivery'].strategy['fail-fast'], false);

  // Every job is bounded. Limits are sized to the real labs, never unbounded.
  const steps = Object.values(workflow.jobs).flatMap((job) => {
    assert.equal(job['runs-on'], 'ubuntu-24.04');
    assert.ok(job['timeout-minutes'] > 0 && job['timeout-minutes'] <= 90);
    return job.steps;
  });
  const actions = new Set(steps.filter((step) => step.uses).map((step) => step.uses.split(' ')[0]));
  assert.deepEqual(actions, expectedActions);
  for (const step of steps.filter((item) => item.uses)) {
    assert.match(step.uses, /^[a-z0-9-]+\/[a-z0-9-]+@[a-f0-9]{40}$/);
    if (step.uses.startsWith('actions/checkout@')) {
      assert.equal(step.with['persist-credentials'], false);
    }
  }

  // Every lab script runs in CI, so a new lab cannot be silently skipped.
  const scripts = await readdir(new URL('../../scripts/', import.meta.url));
  const labScripts = scripts
    .filter((name) => /^run-.+-tests\.py$/.test(name))
    .map((name) => name.slice('run-'.length, -'-tests.py'.length))
    .filter((name) => !['database', 'autonomy-delivery'].includes(name))
    .sort();
  assert.deepEqual([...workflow.jobs.labs.strategy.matrix.lab].sort(), labScripts);
  assert.equal(workflow.jobs.labs.strategy['fail-fast'], false);
  assert.deepEqual(workflow.jobs.services.strategy.matrix.lab, ['openbao', 'keycloak']);

  const commands = steps.filter((step) => step.run).map((step) => step.run);
  for (const required of [
    'npm test',
    'python -m pip check',
    'python -m pytest tests/api tests/identity tests/tooling tests/connectors -q',
    'python scripts/check-gsc-provider-boundary.py',
    'python scripts/run-database-tests.py',
    'python "scripts/run-${{ matrix.lab }}-tests.py"',
    'python "scripts/${{ matrix.lab }}_lab.py"',
    'python scripts/run-autonomy-delivery-tests.py --shard "${{ matrix.shard }}"',
  ]) {
    assert.ok(commands.includes(required), `missing required CI command: ${required}`);
  }
  const static_paths = 'apps/api services/control_plane database/migrations deploy/crawler-network scripts tests';
  assert.ok(commands.includes(`python -m ruff check ${static_paths}`));
  assert.ok(commands.includes(`python -m ruff format --check ${static_paths}`));

  // One required check covers every job: the gate fails unless all succeeded.
  const gate = workflow.jobs.gate;
  assert.equal(gate.if, 'always()');
  assert.deepEqual(
    [...gate.needs].sort(),
    Object.keys(workflow.jobs).filter((name) => name !== 'gate').sort(),
  );
});
