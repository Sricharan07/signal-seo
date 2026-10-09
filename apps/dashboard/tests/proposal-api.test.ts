import assert from "node:assert/strict";
import test from "node:test";

import {
  decideDashboardProposal,
  loadDashboardProposals,
  prepareDashboardProposal,
} from "../lib/proposal-api";
import { TENANT_COOKIE_NAME } from "../lib/browser-auth";

const token = "t".repeat(43);
const csrf = "c".repeat(43);
const siteId = "11111111-1111-4111-8111-111111111111";
const proposalId = "22222222-2222-4222-8222-222222222222";
const revisionId = "33333333-3333-4333-8333-333333333333";
const approvalRequestId = "44444444-4444-4444-8444-444444444444";
const userId = "55555555-5555-4555-8555-555555555555";
const findingId = "66666666-6666-4666-8666-666666666666";
const evidenceId = "77777777-7777-4777-8777-777777777777";
const commandId = "88888888-8888-4888-8888-888888888888";
const revisionSha256 = "a".repeat(64);

function proposalPayload(status = "pending") {
  const decided = status === "pending" || status === "expired" ? null : status;
  return {
    schema_version: 1,
    proposal_id: proposalId,
    revision_id: revisionId,
    revision_number: 1,
    revision_sha256: revisionSha256,
    manifest: {
      schema_version: 1,
      proposal_kind: "local_fixture_metadata_draft",
      site_id: siteId,
      finding: {
        id: findingId,
        evidence_id: evidenceId,
        command_id: commandId,
        resource_locator: "/fixture/missing-meta-description",
        confidence_class: "deterministic",
        observed_at: "2026-09-13T10:00:00Z",
      },
      target: {
        resource_locator: "/fixture/missing-meta-description",
        field: "meta_description",
        before: null,
        after: "Explore the Signal test fixture and its durable SEO evidence.",
      },
      recipe: {
        id: "title_description_improvement",
        version: "local-fixture-0.1.0",
        qualification: "fixture_only",
      },
      assessment: {
        impact: "One synthetic fixture metadata field",
        risk: "low",
        confidence_basis: "Deterministic HTML metadata parser",
        customer_origin_read: false,
      },
      tests: [
        "finding_evidence_bound",
        "target_field_scoped",
        "customer_origin_not_read",
        "external_write_disabled",
      ],
      role_contributions: [
        { role: "technical_seo", release: "local-deterministic-v1", result: "finding_supported" },
        { role: "content_strategy", release: "local-deterministic-v1", result: "metadata_draft_prepared" },
        { role: "independent_reviewer", release: "local-deterministic-v1", result: "scope_checks_passed" },
        { role: "coordinator", release: "local-deterministic-v1", result: "approval_requested" },
      ],
      authority: {
        approval_class: "A1",
        requested: "accept_local_fixture_draft",
        external_write: false,
      },
      cost: { currency: "USD", maximum_minor_units: 0 },
      recovery: {
        mode: "discard_local_draft",
        external_state_changed: false,
        summary: "Discard the draft; no external state has changed.",
      },
    },
    created_by_user_id: userId,
    created_at: "2026-09-13T10:00:00Z",
    approval_request_id: approvalRequestId,
    approval_status: status,
    approval_requested_at: "2026-09-13T10:00:00Z",
    approval_expires_at: "2026-09-14T10:00:00Z",
    decision_id: decided === null ? null : "99999999-9999-4999-8999-999999999999",
    decision: decided,
    decided_by_user_id: decided === null ? null : userId,
    decision_channel: decided === null ? null : "dashboard",
    decided_at: decided === null ? null : "2026-09-13T10:05:00Z",
    reused: false,
  };
}

function verifiedProposalPayload(status = "pending") {
  const payload = proposalPayload(status) as Record<string, any>;
  payload.manifest.proposal_kind = "model_verified_homepage_metadata_draft";
  payload.manifest.finding.resource_locator = "https://acme.example/";
  payload.manifest.target = {
    resource_locator: "https://acme.example/",
    field: "meta_description",
    before: null,
    after: "Understand Acme search performance using the title and heading observed on its verified homepage.",
  };
  payload.manifest.recipe = {
    id: "title_description_improvement",
    version: "verified-homepage-model-1.0.0",
    qualification: "verified_homepage_proposal_only",
  };
  payload.manifest.assessment = {
    impact: "One owner-verified homepage metadata field",
    risk: "low",
    confidence_basis: "Model draft constrained by verified homepage metadata",
    rationale: "The draft uses only the verified homepage title and heading.",
    customer_origin_read: true,
  };
  payload.manifest.tests = [
    "finding_evidence_bound",
    "model_output_schema_valid",
    "verified_homepage_target_scoped",
    "external_write_disabled",
  ];
  payload.manifest.role_contributions[1].release = "verified-gpt-6-luna-metadata-v2";
  payload.manifest.model = {
    call_id: "99999999-9999-4999-8999-999999999999",
    release: "verified-gpt-6-luna-metadata-v2",
    model_requested: "gpt-6-luna",
    model_reported: "gpt-6-luna-2026-09-20",
    provider_response_id: "resp_signal_verified_1",
    prompt_sha256: "b".repeat(64),
    input_sha256: "c".repeat(64),
    output_sha256: "d".repeat(64),
    store: false,
    usage: {
      input_tokens: 120,
      output_tokens: 40,
      cached_input_tokens: 20,
      total_tokens: 160,
    },
  };
  payload.manifest.authority.requested = "accept_verified_homepage_metadata_draft";
  payload.manifest.cost.maximum_minor_units = 1;
  payload.manifest.recovery.summary =
    "Discard the proposed draft; no external state has changed.";
  return payload;
}

function listResponse(status = "pending") {
  return Response.json({
    schema_version: 1,
    site_id: siteId,
    proposals: [proposalPayload(status)],
    correlation_id: "proposal-read",
  });
}

test("loads one strictly validated exact proposal without exposing authority", async () => {
  const calls: Array<[URL | RequestInfo, RequestInit | undefined]> = [];
  const result = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async (input, init) => {
      calls.push([input, init]);
      return listResponse();
    },
  });

  assert.equal(result.state, "available");
  if (result.state !== "available") return;
  assert.equal(result.proposals[0]?.revisionSha256, revisionSha256);
  assert.equal(result.proposals[0]?.externalWrite, false);
  assert.equal(result.proposals[0]?.afterValue, "Explore the Signal test fixture and its durable SEO evidence.");
  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.[1]?.headers instanceof Headers, false);
  assert.match(String((calls[0]?.[1]?.headers as Record<string, string>).Cookie), new RegExp(`^${TENANT_COOKIE_NAME}=`));
});

test("prepares through tenant CSRF and reloads the committed proposal", async () => {
  const requests: RequestInit[] = [];
  const urls: string[] = [];
  const responses = [
    Response.json({ schema_version: 1, csrf_token: csrf }),
    Response.json({
      schema_version: 1,
      site_id: siteId,
      proposal: verifiedProposalPayload(),
      correlation_id: "proposal-prepare",
    }),
    listResponse(),
  ];
  const result = await prepareDashboardProposal({
    tenantToken: token,
    siteId,
    dashboardOrigin: "http://localhost:3000",
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async (input, init) => {
      urls.push(String(input));
      requests.push(init ?? {});
      return responses.shift() ?? new Response(null, { status: 500 });
    },
  });

  assert.equal(result.state, "available");
  assert.equal(requests.length, 3);
  assert.match(urls[1] ?? "", /\/proposals\/verified-homepage$/);
  assert.deepEqual(JSON.parse(String(requests[1]?.body)), { schema_version: 1 });
  assert.equal((requests[1]?.headers as Record<string, string>)["X-CSRF-Token"], csrf);
  assert.equal((requests[1]?.headers as Record<string, string>).Origin, "http://localhost:3000");
});

test("accepts the verified homepage proposal contract", async () => {
  const result = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({
      schema_version: 1,
      site_id: siteId,
      proposals: [verifiedProposalPayload()],
      correlation_id: "verified-model-proposal",
    }),
  });

  assert.equal(result.state, "available");
  if (result.state !== "available") return;
  assert.equal(result.proposals[0]?.proposalKind, "model_verified_homepage_metadata_draft");
  assert.equal(result.proposals[0]?.resourceLocator, "https://acme.example/");
  assert.equal(result.proposals[0]?.model?.release, "verified-gpt-6-luna-metadata-v2");
  assert.equal(result.proposals[0]?.requestedAuthority, "accept_verified_homepage_metadata_draft");
});

test("historical Luna evidence stays readable without being relabelled as a new release", async () => {
  const payload = verifiedProposalPayload() as Record<string, any>;
  payload.manifest.model.release = "verified-gpt-5.6-luna-metadata-v1";
  payload.manifest.role_contributions[1].release = payload.manifest.model.release;
  payload.manifest.model.model_requested = "gpt-5.6-luna";
  payload.manifest.model.model_reported = "gpt-5.6-luna-2026-09-01";
  const result = await loadDashboardProposals({
    tenantToken: token, siteId, baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({schema_version:1,site_id:siteId,proposals:[payload],correlation_id:"synthetic-history"}),
  });
  assert.equal(result.state, "available");
  if (result.state === "available") {
    assert.equal(result.proposals[0]?.model?.release, "verified-gpt-5.6-luna-metadata-v1");
  }
});

test("accepts the exact Luna model manifest and exposes its audit identity", async () => {
  const payload = proposalPayload() as Record<string, any>;
  payload.manifest.proposal_kind = "model_fixture_metadata_draft";
  payload.manifest.target.after =
    "Explore Signal's synthetic fixture through supervised metadata grounded in exact local evidence.";
  payload.manifest.recipe.version = "local-model-0.1.0";
  payload.manifest.assessment = {
    impact: "One synthetic fixture metadata field",
    risk: "low",
    confidence_basis: "Model draft constrained by deterministic fixture evidence",
    rationale: "The draft uses only facts from the bounded synthetic evidence packet.",
    customer_origin_read: false,
  };
  payload.manifest.tests = [
    "finding_evidence_bound",
    "model_output_schema_valid",
    "target_field_scoped",
    "external_write_disabled",
  ];
  payload.manifest.role_contributions[1].release = "local-gpt-6-luna-metadata-v2";
  payload.manifest.model = {
    call_id: "99999999-9999-4999-8999-999999999999",
    release: "local-gpt-6-luna-metadata-v2",
    model_requested: "gpt-6-luna",
    model_reported: "gpt-6-luna-2026-09-01",
    provider_response_id: "resp_signal_local_1",
    prompt_sha256: "b".repeat(64),
    input_sha256: "c".repeat(64),
    output_sha256: "d".repeat(64),
    store: false,
    usage: {
      input_tokens: 120,
      output_tokens: 40,
      cached_input_tokens: 20,
      total_tokens: 160,
    },
  };
  payload.manifest.authority.requested = "accept_model_fixture_draft";
  payload.manifest.cost.maximum_minor_units = 1;
  const result = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({
      schema_version: 1,
      site_id: siteId,
      proposals: [payload],
      correlation_id: "model-proposal",
    }),
  });

  assert.equal(result.state, "available");
  if (result.state !== "available") return;
  assert.equal(result.proposals[0]?.model?.modelRequested, "gpt-6-luna");
  assert.equal(result.proposals[0]?.model?.providerResponseId, "resp_signal_local_1");
  assert.equal(result.proposals[0]?.model?.usage.totalTokens, 160);
  assert.equal(result.proposals[0]?.model?.store, false);
  assert.equal(result.proposals[0]?.maximumCostMinorUnits, 1);
});

test("decides only the supplied revision and keeps conflicts closed", async () => {
  const decisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  let conflictCalls = 0;
  const requests: RequestInit[] = [];
  const responses = [
    Response.json({ schema_version: 1, csrf_token: csrf }),
    Response.json({
      schema_version: 1,
      site_id: siteId,
      proposal: proposalPayload("approved"),
      correlation_id: "proposal-decision",
    }),
    listResponse("approved"),
  ];
  const result = await decideDashboardProposal({
    tenantToken: token,
    siteId,
    approvalRequestId,
    revisionSha256,
    decisionId,
    decision: "approved",
    dashboardOrigin: "http://localhost:3000",
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async (_input, init) => {
      requests.push(init ?? {});
      return responses.shift() ?? new Response(null, { status: 500 });
    },
  });
  assert.equal(result.state, "available");
  assert.deepEqual(JSON.parse(String(requests[1]?.body)), {
    schema_version: 1,
    revision_sha256: revisionSha256,
    decision_id: decisionId,
    decision: "approved",
  });

  const conflict = await decideDashboardProposal({
    tenantToken: token,
    siteId,
    approvalRequestId,
    revisionSha256,
    decisionId,
    decision: "approved",
    dashboardOrigin: "http://localhost:3000",
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () =>
      conflictCalls++ === 0
        ? Response.json({ schema_version: 1, csrf_token: csrf })
        : new Response(null, { status: 409 }),
  });
  assert.equal(conflict.state, "conflict");
});

test("rejects malformed proposal projections and invalid local inputs", async () => {
  const malformed = proposalPayload() as Record<string, unknown>;
  malformed.extra = true;
  const result = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({
      schema_version: 1,
      site_id: siteId,
      proposals: [malformed],
      correlation_id: "bad",
    }),
  });
  assert.equal(result.state, "invalid");

  const mismatchedRole = proposalPayload() as Record<string, any>;
  mismatchedRole.manifest.role_contributions[0].result = "approval_requested";
  const invalidRole = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({
      schema_version: 1,
      site_id: siteId,
      proposals: [mismatchedRole],
      correlation_id: "bad-role",
    }),
  });
  assert.equal(invalidRole.state, "invalid");

  const insecureVerifiedTarget = verifiedProposalPayload() as Record<string, any>;
  insecureVerifiedTarget.manifest.target.resource_locator = "http://acme.example/";
  const invalidVerifiedTarget = await loadDashboardProposals({
    tenantToken: token,
    siteId,
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => Response.json({
      schema_version: 1,
      site_id: siteId,
      proposals: [insecureVerifiedTarget],
      correlation_id: "bad-verified-target",
    }),
  });
  assert.equal(invalidVerifiedTarget.state, "invalid");

  const noDispatch = await prepareDashboardProposal({
    tenantToken: "bad",
    siteId,
    dashboardOrigin: "http://localhost:3000",
    baseUrl: "http://127.0.0.1:8000",
    fetcher: async () => {
      throw new Error("must not dispatch");
    },
  });
  assert.equal(noDispatch.state, "invalid");
});
