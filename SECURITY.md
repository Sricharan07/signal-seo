# Security Policy

Signal acts on websites, repositories and connected accounts, so security reports
get priority over everything else.

## Reporting a vulnerability

Do not open a public issue, pull request or discussion for a vulnerability.

Report it privately through GitHub: on this repository, open the **Security** tab
and choose **Report a vulnerability**. Include:

- what an attacker can do, and against which component (dashboard, API, worker,
  database function, egress, sandbox, connector);
- the steps or a minimal proof of concept;
- the version or commit you tested.

You will get an acknowledgement within 3 business days and an assessment within
10 business days. Fixes for confirmed issues are released as soon as they are
verified. You are credited in the advisory unless you ask not to be.

## Scope

In scope: everything in this repository, including the self-hosted deployment
files. Issues that weaken a product invariant are always treated as security
issues, for example:

- anything that lets a model, a page, a document or a connector mint, enlarge or
  exercise authority;
- anything that lets Signal merge, deploy, push to a default branch, delete
  content, edit CI workflows or read repository secrets;
- tenant or site isolation failures, credential exposure, egress-control bypass,
  browser sandbox escape, or approval of anything other than the exact reviewed
  revision.

Out of scope: findings that need an already-compromised host or administrator
account, denial of service by volume, and reports from automated scanners without
a demonstrated impact.

## Safe harbor

Good-faith research that respects this policy, avoids privacy violations and data
destruction, and uses only your own accounts and sites is welcome. Do not test
against sites or accounts you do not own.

## Supported versions

Signal is pre-release. Only the latest commit on `main` receives security fixes.
