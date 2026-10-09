# Slice 0001: Repository and Documentation Baseline

## Scope

Preserve the two supplied specifications and establish documentation, local tests,
dependency locking, formatting conventions, and slice-by-slice commits. This is
repository tooling, not an application implementation.

Traceability: specification sections 30 and 34; REQ-017; G-00 (partial evidence only).

## Implementation

- `scripts/check-repository.mjs` checks local Markdown links and fragments using
  parsed Markdown, rejects file links escaping the repository, parses JSON/YAML
  examples, and protects both specification baselines by SHA-256.
- `tests/repository/` tests the checker with valid and intentionally invalid inputs.
- `package-lock.json` fixes the development dependency graph.
- `.editorconfig`, `.gitignore`, and `AGENTS.md` define local maintenance rules.
- ADR-0001 records why implementation status is maintained separately from the spec.

## Verification

```sh
npm ci
npm test
npm audit --audit-level=high
```

Local result, 2026-09-07: all seven repository unit tests and checks of all 11
Markdown files passed. The npm audit reported zero known vulnerabilities. This is
a point-in-time development dependency check, not a production security review.

The checker does not execute code embedded in Markdown, make external web requests,
or certify linked provider claims. Missing references, malformed examples, unsafe
local paths, and modified baselines must fail the tests rather than be ignored.

## Limitations

No application test runner, CI execution evidence, production credentials, license,
or remote repository is introduced in this slice. Subsequent slices must document
their own runtime tests and qualification boundaries.
