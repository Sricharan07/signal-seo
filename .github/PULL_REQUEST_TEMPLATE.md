## Summary

<!-- What changes and why. Link the issue: "Closes #123". -->

## Classification

<!-- See AGENTS.md "How To Work". -->
- [ ] **Product**: uses existing authority without changing it.
- [ ] **Security-critical**: touches identity, tenancy, secrets, egress, browser
      sandbox, autonomy gate, standing authorization, external writes, publishing
      authority or recovery.

## Tests

- [ ] Positive, negative and failure paths are covered.
- [ ] `npm test` passes.
- [ ] The Python checks and labs for the touched area pass (see CONTRIBUTING.md).
      List the commands you ran and their counts:

```text

```

## Safety checklist

- [ ] No model, page, document or connector can mint, enlarge or exercise authority.
- [ ] Signal still never merges, deploys, pushes to a default branch, deletes
      content, edits CI workflows or reads repository secrets.
- [ ] No secrets, credentials, personal data or environment-specific identifiers.
- [ ] Unavailable capabilities stay visibly unavailable; nothing simulates readiness.
- [ ] No existing migration or accepted specification revision was modified.

## Records

- [ ] Status line and CHANGELOG updated.
- [ ] Security-critical only: implementation record, ADR (if warranted) and evidence.

## Contributor License Agreement

- [ ] I have signed, or will sign when asked, the [CLA](../CLA.md).
