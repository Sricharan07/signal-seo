import { spawn } from 'node:child_process';
import { createHash, randomBytes } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const project = `signal-wp-lab-${randomBytes(6).toString('hex')}`;
const env = {
  ...process.env,
  SIGNAL_LAB_PASSWORD: randomBytes(32).toString('hex'),
  SIGNAL_LAB_ROOT_PASSWORD: randomBytes(32).toString('hex'),
};
const base = ['compose', '--project-name', project, '--file',
  path.join(root, 'experiments/wordpress-feasibility/compose.yaml')];
let interrupted = false;
let active;
let snapshot;
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
  interrupted = true;
  active?.kill('SIGTERM');
});

function docker(args, { capture = false, timeout = 600_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn('docker', [...base, ...args], {
      env, cwd: root, stdio: ['ignore', capture ? 'pipe' : 'inherit', 'inherit'],
    });
    active = child;
    let output = '';
    const timer = setTimeout(() => child.kill('SIGKILL'), timeout);
    child.stdout?.on('data', (chunk) => {
      output += chunk;
      if (output.length > 2_000_000) child.kill('SIGKILL');
    });
    child.once('error', (error) => { clearTimeout(timer); reject(error); });
    child.once('close', (code) => {
      clearTimeout(timer);
      active = undefined;
      if (code !== 0) reject(new Error(`Lab command ${args[0]} failed (exit ${code}).`));
      else resolve(output);
    });
  });
}

try {
  // Fully read source before mounting it; a run must not observe later source edits.
  snapshot = await mkdtemp(path.join(tmpdir(), 'signal-wp-lab-'));
  const source = path.join(root, 'experiments/wordpress-feasibility/src');
  const sourceHashes = {};
  for (const filename of await readdir(source)) {
    const bytes = await readFile(path.join(source, filename));
    if (bytes.length === 0) throw new Error(`Empty lab source: ${filename}`);
    await writeFile(path.join(snapshot, filename), bytes);
    sourceHashes[filename] = createHash('sha256').update(bytes).digest('hex');
  }
  env.SIGNAL_LAB_SOURCE = snapshot;
  process.stdout.write(`Starting isolated project ${project}; no host ports or customer data.\n`);
  await docker(['up', '--detach', '--wait', '--wait-timeout', '180']);
  if (interrupted) throw new Error('Lab interrupted.');
  await docker(['exec', '-T', 'wordpress', 'php', '/lab/install.php']);
  const output = await docker(['exec', '-T', 'wordpress', 'php', '/lab/test.php'], { capture: true });
  const report = JSON.parse(output);
  if (!Array.isArray(report.tests) || report.tests.length === 0 || !Number.isInteger(report.failed)) {
    throw new Error('Malformed or empty lab report.');
  }
  report.source_sha256 = sourceHashes;
  const directory = path.join(root, '.runtime/wordpress-feasibility');
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, 'latest.json'), `${JSON.stringify(report, null, 2)}\n`);
  for (const result of report.tests) process.stdout.write(`${result.status}: ${result.name}${result.error ? `: ${result.error}` : ''}\n`);
  process.stdout.write(`${report.passed}/${report.tests.length} passed. Report: ${directory}/latest.json\n`);
  if (report.failed || interrupted) process.exitCode = 1;
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
} finally {
  // The random, invocation-owned project is the only cleanup target.
  try {
    if (env.SIGNAL_LAB_SOURCE) await docker(['down', '--volumes', '--remove-orphans'], { timeout: 120_000 });
  }
  catch (error) {
    process.stderr.write(`Cleanup failed for ${project}: ${error.message}\n`);
    process.exitCode = 1;
  }
  if (snapshot) await rm(snapshot, { recursive: true, force: true });
}
