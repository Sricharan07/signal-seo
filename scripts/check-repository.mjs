import { createHash } from 'node:crypto';
import { readdir, readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import MarkdownIt from 'markdown-it';
import GithubSlugger from 'github-slugger';
import { parseDocument } from 'yaml';

const parser = new MarkdownIt({ html: true });
// Audit disallowed schemes as tokens; this parser never renders HTML.
parser.validateLink = () => true;
const excluded = new Set(['.git', 'node_modules', '.runtime', '.venv', '.next', 'coverage']);
const baselines = {
  'Signal_Production_Engineering_Specification_Final.md': 'e9b6fa79a93c84427bfcf100e3b80dcebdfd7e08b6d107df3181a975863c3676',
  'Signal_Production_Engineering_Specification_Corrected.md': '14421ec851a4241718b9229e6d53dd3217bc48eb5d62fa7bb2265f0f7d604899',
  'Signal_Production_Engineering_Specification_Revision_3_2.md': '74181cb8ea1e3c902f3de3055add9f70fdaa9f097f24bf8bf0180aeee68b6cd7',
  'Signal_Production_Engineering_Specification_Revision_4_0.md': 'a1be2da9549381914a2e1c0996ffa9efb795c9aefefbd41a4a3bb888ccef2cca',
  'Signal_Production_Engineering_Specification_Revision_4_1.md': '0b641fbf34222b2c6b0623cd9602d02a76e834a3fe9c7c79aec4598a39691b53',
};

export function inspectMarkdown(source) {
  const tokens = parser.parse(source, {});
  const anchors = new Set();
  const links = [];
  const examples = [];
  const errors = [];
  const slugger = new GithubSlugger();
  const collect = (token) => {
    if (token.type === 'link_open') links.push(token.attrGet('href'));
    if (token.type === 'image') links.push(token.attrGet('src'));
    if (token.type.startsWith('html_')) {
      for (const match of token.content.matchAll(/\bid=["']([^"']+)["']/g)) {
        if (anchors.has(match[1])) errors.push(`Duplicate explicit anchor: ${match[1]}`);
        anchors.add(match[1]);
      }
    }
    for (const child of token.children ?? []) collect(child);
  };
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.type === 'heading_open') {
      const content = tokens[index + 1];
      const text = (content.children ?? []).map((child) => child.content).join('');
      anchors.add(slugger.slug(text));
    }
    collect(token);
    if (token.type === 'fence' && ['json', 'yaml', 'yml'].includes(token.info.trim())) {
      examples.push(token);
      try {
        if (token.info.trim() === 'json') JSON.parse(token.content);
        else {
          const document = parseDocument(token.content, { uniqueKeys: true });
          if (document.errors.length) throw document.errors[0];
        }
      } catch (error) {
        errors.push(`Invalid ${token.info} example at line ${token.map[0] + 1}: ${error.message}`);
      }
    }
  }
  return { anchors, links, examples: examples.length, errors };
}

async function markdownFiles(directory) {
  const found = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (excluded.has(entry.name)) continue;
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...await markdownFiles(filename));
    else if (entry.isFile() && entry.name.endsWith('.md')) found.push(filename);
  }
  return found.sort();
}

export async function checkRepository(root, { protectBaselines = true } = {}) {
  root = await realpath(root);
  const errors = [];
  const documents = new Map();
  for (const filename of await markdownFiles(root)) {
    const document = inspectMarkdown(await readFile(filename, 'utf8'));
    documents.set(filename, document);
    errors.push(...document.errors.map((error) => `${path.relative(root, filename)}: ${error}`));
  }
  for (const [filename, document] of documents) {
    for (const target of document.links) {
      if (/^(https?:|mailto:)/i.test(target)) continue;
      try {
        if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('//')) {
          throw new Error('Unsupported local link scheme');
        }
        const [file, fragment] = target.split('#');
        const candidate = file ? path.resolve(path.dirname(filename), decodeURIComponent(file)) : filename;
        const resolved = await realpath(candidate);
        if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) {
          throw new Error('Local link escapes repository');
        }
        if (fragment && resolved.endsWith('.md')) {
          const linked = documents.get(resolved) ?? inspectMarkdown(await readFile(resolved, 'utf8'));
          if (!linked.anchors.has(decodeURIComponent(fragment))) throw new Error('Missing fragment');
        }
      } catch (error) {
        errors.push(`${path.relative(root, filename)}: ${target}: ${error.message}`);
      }
    }
  }
  if (protectBaselines) {
    for (const [filename, expected] of Object.entries(baselines)) {
      try {
        const hash = createHash('sha256').update(await readFile(path.join(root, filename))).digest('hex');
        if (hash !== expected) errors.push(`Protected specification changed: ${filename}`);
      } catch {
        errors.push(`Missing protected specification: ${filename}`);
      }
    }
  }
  return { files: documents.size, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await checkRepository(process.cwd());
  if (result.errors.length) {
    process.stderr.write(`${result.errors.join('\n')}\n`);
    process.exitCode = 1;
  } else process.stdout.write(`Documentation checks passed (${result.files} Markdown files).\n`);
}
