import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm, symlink } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkRepository, inspectMarkdown } from '../../scripts/check-repository.mjs';

test('parses headings, explicit anchors, references, images, and ignores code links', () => {
  const result = inspectMarkdown('# Hello `world`\n# Hello `world`\n<a id="exact"></a>\n[x][ref]\n![image](asset.png)\n\n[ref]: page.md#target\n\n```text\n[not a link](missing)\n```');
  assert.deepEqual([...result.anchors], ['hello-world', 'hello-world-1', 'exact']);
  assert.deepEqual(result.links, ['page.md#target', 'asset.png']);
  assert.deepEqual(result.errors, []);
});

test('validates structured examples without executing embedded code', () => {
  assert.equal(inspectMarkdown('```json\n{"ok": true}\n```\n```yaml\na: 1\n```').examples, 2);
  assert.equal(inspectMarkdown('```json\n{broken}\n```').errors.length, 1);
  assert.equal(inspectMarkdown('```yaml\na: 1\na: 2\n```').errors.length, 1);
  assert.equal(inspectMarkdown('```python\nraise Exception("do not execute")\n```').errors.length, 0);
});

test('duplicate explicit anchors are errors', () => {
  assert.equal(inspectMarkdown('<a id="x"></a>\n<a id="x"></a>').errors.length, 1);
});

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'signal-docs-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'docs'));
  return root;
}

test('resolves nested and percent-encoded local paths and fragments', async (t) => {
  const root = await fixture(t);
  await writeFile(path.join(root, 'README.md'), '[page](docs/a%20page.md#hello)\n[web](https://example.com)');
  await writeFile(path.join(root, 'docs/a page.md'), '# Hello\n[home](../README.md)');
  assert.deepEqual((await checkRepository(root, { protectBaselines: false })).errors, []);
});

test('missing files and fragments fail', async (t) => {
  const root = await fixture(t);
  await writeFile(path.join(root, 'README.md'), '[missing](no.md)\n[bad](#absent)');
  assert.equal((await checkRepository(root, { protectBaselines: false })).errors.length, 2);
});

test('symlink escape and local file schemes fail', async (t) => {
  const root = await fixture(t);
  await symlink(os.tmpdir(), path.join(root, 'outside'));
  await writeFile(path.join(root, 'README.md'), '[bad](outside/)\n[file](file:///etc/hosts)');
  assert.equal((await checkRepository(root, { protectBaselines: false })).errors.length, 2);
});

test('accepted revision protection fails for missing or modified specifications', async (t) => {
  const root = await fixture(t);
  await writeFile(path.join(root, 'Signal_Production_Engineering_Specification_Final.md'), '# Altered');
  const { errors } = await checkRepository(root);
  assert.ok(errors.includes(
    'Protected specification changed: Signal_Production_Engineering_Specification_Final.md',
  ));
  assert.ok(errors.includes(
    'Missing protected specification: Signal_Production_Engineering_Specification_Corrected.md',
  ));
  assert.ok(errors.includes(
    'Missing protected specification: Signal_Production_Engineering_Specification_Revision_3_2.md',
  ));
  assert.ok(errors.includes(
    'Missing protected specification: Signal_Production_Engineering_Specification_Revision_4_0.md',
  ));
  assert.ok(errors.includes(
    'Missing protected specification: Signal_Production_Engineering_Specification_Revision_4_1.md',
  ));
});

test('preserved Core V1 contract stays explicit and does not claim implementation', async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const filenames = {
    specification: 'Signal_Production_Engineering_Specification_Revision_3_2.md',
    prd: 'docs/product/core-v1-prd.md',
    roadmap: 'docs/implementation/core-v1-roadmap.md',
    decision: 'docs/adr/0030-github-first-core-v1.md',
    record: 'docs/implementation/0030-core-v1-contract-revision.md',
  };
  const documents = Object.fromEntries(await Promise.all(
    Object.entries(filenames).map(async ([name, filename]) => [
      name,
      await readFile(path.join(root, filename), 'utf8'),
    ]),
  ));
  const roles = [
    'Coordinator / Planner',
    'Technical SEO Specialist',
    'Analytics Specialist',
    'Competitor Research Specialist',
    'Content Strategy Specialist',
    'Performance Specialist',
    'Repository Implementer',
    'Independent Reviewer / Verifier',
    'Communicator',
  ];

  assert.match(documents.specification, /\*\*Revision:\*\* 3\.2/);
  assert.match(documents.specification, /CORE_V1_PILOT_ADMISSION/);
  assert.doesNotMatch(documents.specification, /optional GitHub/);
  const numbered = (pattern) => [...documents.specification.matchAll(pattern)]
    .map((match) => Number(match[1]));
  assert.deepEqual(numbered(/\| REQ-(\d{3}):/g), Array.from({ length: 18 }, (_, i) => i + 1));
  assert.deepEqual(numbered(/\| INV-(\d{3}) \|/g), Array.from({ length: 25 }, (_, i) => i + 1));
  assert.deepEqual(numbered(/\| EC-(\d{3}) \|/g), Array.from({ length: 123 }, (_, i) => i + 1));
  assert.deepEqual(numbered(/\| G-(\d{2}):/g), Array.from({ length: 13 }, (_, i) => i));
  assert.deepEqual(numbered(/^### Milestone (\d):/gm), Array.from({ length: 9 }, (_, i) => i));
  for (const role of roles) {
    const normalizedRole = role.replaceAll(' ', '');
    assert.ok(
      documents.specification.replaceAll(' ', '').includes(normalizedRole),
      `specification omits ${role}`,
    );
    assert.ok(
      documents.prd.replaceAll(' ', '').includes(normalizedRole),
      `PRD omits ${role}`,
    );
  }
  for (const resource of ['Google Search Console', 'GitHub', 'Telegram', 'dashboard']) {
    assert.ok(documents.prd.includes(resource), `PRD omits ${resource}`);
    assert.ok(documents.roadmap.includes(resource), `roadmap omits ${resource}`);
  }
  assert.match(documents.prd, /CMS-specific teams and production CMS writes are deferred/i);
  assert.match(documents.prd, /no merge or deployment authority/i);
  assert.match(documents.record, /NO NEW RUNTIME CAPABILITY/);
  assert.match(documents.decision, /nine premature microservices/);
  assert.match(documents.decision, /superseded by \[ADR-0060\]/);
  assert.match(documents.prd, /Superseded 2026-09-24/);
  assert.match(documents.roadmap, /Superseded 2026-09-24/);
});

test('active Revision 4.0 direction is explicit and does not claim implementation', async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const filenames = {
    specification: 'Signal_Production_Engineering_Specification_Revision_4_0.md',
    prd: 'docs/product/prd.md',
    roadmap: 'docs/implementation/roadmap.md',
    decision: 'docs/adr/0060-autonomous-seo-employee-direction.md',
    decisions: 'docs/adr/README.md',
    record: 'docs/implementation/0063-autonomous-direction-revision.md',
    status: 'docs/implementation/status.md',
    index: 'docs/README.md',
    readme: 'README.md',
    agents: 'AGENTS.md',
    claude: 'CLAUDE.md',
    product: 'PRODUCT.md',
  };
  const documents = Object.fromEntries(await Promise.all(
    Object.entries(filenames).map(async ([name, filename]) => [
      name,
      await readFile(path.join(root, filename), 'utf8'),
    ]),
  ));
  const spec = documents.specification;
  const numbered = (pattern) => [...spec.matchAll(pattern)].map((match) => Number(match[1]));
  const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => from + i);

  assert.match(spec, /\*\*Revision:\*\* 4\.0/);
  assert.match(spec, /\*\*Amends:\*\* Revision 3\.2/);
  assert.deepEqual(numbered(/\| REQ-(\d{3}):/g), range(19, 27));
  assert.match(spec, /\| REQ-016 \(amended\):/);
  assert.deepEqual(numbered(/\| INV-(\d{3}) \|/g), range(26, 34));
  assert.deepEqual(numbered(/\| EC-(\d{3}) \|/g), range(124, 146));
  assert.deepEqual(numbered(/<a id="section-(\d+)"><\/a>/g), range(1, 22));
  assert.match(spec, /can only reduce autonomy/);
  assert.match(spec, /Signal never merges, deploys, pushes to a default branch/);
  assert.match(spec, /`NOT_EXECUTED`/);
  assert.match(spec, /No release is\s+admitted and no production write is enabled/);

  for (const release of ['R1 Insight', 'R2 Work', 'R3 Employee', 'R4 AI Search']) {
    for (const name of ['specification', 'prd', 'roadmap', 'readme', 'status']) {
      assert.ok(documents[name].includes(release), `${name} omits ${release}`);
    }
  }
  for (const connector of [
    'Google Search Console', 'GitHub', 'Bing Webmaster Tools', 'Brand documents', 'IndexNow',
    'Slack', 'Telegram', 'Email', 'Google Analytics 4', 'Webflow', 'WordPress', 'Notion',
  ]) {
    assert.ok(spec.includes(`| ${connector}`), `specification omits connector ${connector}`);
    assert.ok(documents.prd.includes(connector), `PRD omits connector ${connector}`);
  }
  for (const concept of ['Jev', 'standing authorization', 'egress proxy', 'DataForSEO', 'Business Brain']) {
    assert.ok(spec.includes(concept), `specification omits ${concept}`);
    assert.ok(documents.agents.includes(concept) || documents.prd.includes(concept), `agent brief omits ${concept}`);
  }

  assert.match(documents.prd, /ACCEPTED 2026-09-24/);
  assert.match(documents.roadmap, /ACTIVE SEQUENCE; NOT A RELEASE CERTIFICATE/);
  assert.match(documents.roadmap, /## Next Slice/);
  assert.match(documents.decision, /Supersedes: The Core V1 scope, deferrals, and milestone sequence in \[ADR-0030\]/);
  assert.match(documents.decision, /Autonomy Decided By Model Confidence Alone/);
  assert.match(documents.decisions, /\[0060\]\(0060-autonomous-seo-employee-direction\.md\)/);
  assert.match(documents.record, /NO NEW RUNTIME CAPABILITY/);
  assert.match(documents.status, /No R1–R4 release, Core V1 pilot, or GA1 release is approved/);
  assert.match(documents.index, /Revision 4\.0 takes precedence/);
  assert.match(documents.readme, /Revision 4\.0 takes precedence/);
  assert.doesNotMatch(documents.readme, /Revision 3\.2 takes precedence/);
  assert.match(documents.readme, /product releases R1–R4 not yet implemented/);
  for (const link of [
    'Signal_Production_Engineering_Specification_Revision_4_0.md',
    'docs/product/prd.md',
    'docs/implementation/roadmap.md',
    'docs/implementation/status.md',
  ]) {
    assert.ok(documents.agents.includes(`](${link})`), `AGENTS.md omits ${link}`);
  }
  assert.match(documents.agents, /the Jev gate can only keep or reduce autonomy/);
  assert.match(documents.claude, /^@AGENTS\.md$/m);
  assert.match(documents.product, /autonomous, evidence-backed SEO and AI-search employee/);
});
