import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
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

  const steps = Object.values(workflow.jobs).flatMap((job) => {
    assert.equal(job['runs-on'], 'ubuntu-24.04');
    assert.ok(job['timeout-minutes'] > 0 && job['timeout-minutes'] <= 15);
    return job.steps;
  });
  const actions = new Set(steps.filter((step) => step.uses).map((step) => step.uses));
  assert.deepEqual(actions, expectedActions);
  for (const step of steps.filter((item) => item.uses)) {
    assert.match(step.uses, /^[a-z0-9-]+\/[a-z0-9-]+@[a-f0-9]{40}$/);
    if (step.uses.startsWith('actions/checkout@')) {
      assert.equal(step.with['persist-credentials'], false);
    }
  }

  const commands = steps.filter((step) => step.run).map((step) => step.run);
  for (const required of [
    'npm ci --ignore-scripts',
    'npm test',
    'python -m pip check',
    'python -m pytest tests/api tests/identity tests/tooling -q',
    'python scripts/openbao_lab.py',
    'python scripts/keycloak_lab.py',
    'python scripts/run-temporal-tests.py',
    'python scripts/run-consumer-tests.py',
    'python scripts/run-workflow-consumer-image-tests.py',
    'python scripts/run-candidate-sandbox-tests.py',
    'python scripts/run-crawler-network-tests.py',
    'python scripts/run-page-attempt-tests.py',
    'python scripts/run-database-tests.py',
    'python scripts/run-authority-journal-tests.py',
    'python scripts/run-autonomy-delivery-tests.py --shard "${{ matrix.shard }}"',
  ]) {
    assert.ok(commands.includes(required), `missing required CI command: ${required}`);
  }
  assert.ok(commands.some((command) => command.startsWith('python -m ruff check ')));
  assert.ok(commands.some((command) => command.startsWith('python -m ruff format --check ')));
  for (const path of [
    'apps/api',
    'tests/api',
    'tests/consumer',
    'tests/container',
    'tests/crawler',
    'tests/delivery',
    'tests/page_attempt',
    'deploy/crawler-network',
    'tests/identity',
    'tests/temporal',
  ]) {
    assert.ok(commands.some((command) => command.startsWith('python -m ruff check ') && command.includes(path)));
    assert.ok(
      commands.some((command) => command.startsWith('python -m ruff format --check ') && command.includes(path)),
    );
  }
});
