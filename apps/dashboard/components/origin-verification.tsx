"use client";

import {
  Check,
  Clipboard,
  ExternalLink,
  FileCheck2,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";

import type {
  DashboardOriginChallenge,
  DashboardOriginVerification,
  OriginChallengeResult,
  OriginVerificationResult,
} from "../lib/origin-verification-api";

interface OriginVerificationProps {
  siteId: string;
  siteName: string;
  origin: string;
  ownershipStatus: "unverified" | "reverification_required";
}

type PendingOperation = "issue" | "verify" | null;

export function OriginVerification({
  siteId,
  siteName,
  origin,
  ownershipStatus,
}: OriginVerificationProps) {
  const [challenge, setChallenge] = useState<DashboardOriginChallenge | null>(
    null,
  );
  const [verification, setVerification] =
    useState<DashboardOriginVerification | null>(null);
  const [pending, setPending] = useState<PendingOperation>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function issueChallenge() {
    setPending("issue");
    setMessage(null);
    try {
      const response = await fetch("/auth/origin-challenge", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          schema_version: 1,
          site_id: siteId,
          origin,
          idempotency_key: crypto.randomUUID(),
        }),
        signal: AbortSignal.timeout(17_000),
      });
      const result = (await response.json()) as OriginChallengeResult;
      if (response.status === 201 && result.state === "issued") {
        setChallenge(result.challenge);
        setVerification(null);
        setMessage("Challenge issued. Publish both values exactly as shown.");
      } else {
        setMessage(failureMessage(result.state, "issue"));
      }
    } catch {
      setMessage("The challenge service could not be reached. Try again later.");
    } finally {
      setPending(null);
    }
  }

  async function verifyOrigin() {
    if (challenge === null) return;
    setPending("verify");
    setMessage(null);
    try {
      const response = await fetch("/auth/verify-origin", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          schema_version: 1,
          site_id: siteId,
          challenge_id: challenge.challengeId,
          origin,
          idempotency_key: crypto.randomUUID(),
        }),
        signal: AbortSignal.timeout(17_000),
      });
      const result = (await response.json()) as OriginVerificationResult;
      if (response.status === 200 && result.state === "verified") {
        setVerification(result.verification);
        setMessage("Exact public-origin control verified.");
        window.location.reload();
      } else {
        setMessage(failureMessage(result.state, "verify"));
      }
    } catch {
      setMessage("The proof check could not be completed. No authority was granted.");
    } finally {
      setPending(null);
    }
  }

  async function copyValue(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setMessage(`${label} copied.`);
    } catch {
      setMessage(`${label} could not be copied. Select it manually.`);
    }
  }

  if (verification !== null) {
    return (
      <section className="origin-verification verified" aria-label="Origin verified">
        <ShieldCheck size={18} aria-hidden="true" />
        <div>
          <strong>Exact origin verified</strong>
          <p>
            {origin} is bound to {siteName}. Recheck due{" "}
            <time dateTime={verification.recheckAt}>
              {formatDate(verification.recheckAt)}
            </time>
            .
          </p>
        </div>
      </section>
    );
  }

  return (
    <section
      className="origin-verification"
      aria-labelledby="origin-verification-title"
    >
      <header>
        <div>
          <h3 id="origin-verification-title">
            {ownershipStatus === "reverification_required"
              ? "Reverify public origin"
              : "Verify public origin"}
          </h3>
          <p>
            Prove control of {origin} with one exact plaintext file. Redirects
            and alternate hosts are rejected.
          </p>
        </div>
        {challenge === null ? (
          <button
            className="primary-command"
            type="button"
            disabled={pending !== null}
            onClick={issueChallenge}
          >
            {pending === "issue" ? (
              <LoaderCircle className="spin" size={16} aria-hidden="true" />
            ) : (
              <FileCheck2 size={16} aria-hidden="true" />
            )}
            {pending === "issue" ? "Issuing" : "Issue proof"}
          </button>
        ) : null}
      </header>

      {challenge === null ? null : (
        <div className="origin-proof">
          <div className="proof-step">
            <span className="proof-number">1</span>
            <div>
              <strong>Publish this file URL</strong>
              <div className="proof-value">
                <code>{challenge.proofUrl}</code>
                <button
                  className="copy-command"
                  type="button"
                  aria-label="Copy proof file URL"
                  title="Copy URL"
                  onClick={() => copyValue(challenge.proofUrl, "Proof URL")}
                >
                  <Clipboard size={16} aria-hidden="true" />
                </button>
                <a
                  className="copy-command"
                  href={challenge.proofUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Open proof URL in a new tab"
                  title="Open proof URL"
                >
                  <ExternalLink size={16} aria-hidden="true" />
                </a>
              </div>
            </div>
          </div>
          <div className="proof-step">
            <span className="proof-number">2</span>
            <div>
              <strong>Use this exact file content</strong>
              <div className="proof-value">
                <code>{challenge.proofContent.trimEnd()}</code>
                <button
                  className="copy-command"
                  type="button"
                  aria-label="Copy exact proof file content"
                  title="Copy file content"
                  onClick={() =>
                    copyValue(challenge.proofContent, "Proof content")
                  }
                >
                  <Clipboard size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          </div>
          <div className="proof-footer">
            <span>
              Expires{" "}
              <time dateTime={challenge.expiresAt}>
                {formatDateTime(challenge.expiresAt)}
              </time>
            </span>
            <div className="proof-actions">
              <button
                className="secondary-command"
                type="button"
                disabled={pending !== null}
                onClick={issueChallenge}
              >
                <RefreshCw size={16} aria-hidden="true" /> New proof
              </button>
              <button
                className="primary-command"
                type="button"
                disabled={pending !== null}
                onClick={verifyOrigin}
              >
                {pending === "verify" ? (
                  <LoaderCircle className="spin" size={16} aria-hidden="true" />
                ) : (
                  <Check size={16} aria-hidden="true" />
                )}
                {pending === "verify" ? "Checking" : "Verify now"}
              </button>
            </div>
          </div>
        </div>
      )}

      {message === null ? null : (
        <div className="origin-message" role="status" aria-live="polite">
          {message.includes("verified") ||
          message.includes("copied") ||
          message.includes("issued") ? (
            <Check size={15} aria-hidden="true" />
          ) : (
            <TriangleAlert size={15} aria-hidden="true" />
          )}
          <span>{message}</span>
        </div>
      )}
    </section>
  );
}

function failureMessage(
  state: OriginChallengeResult["state"] | OriginVerificationResult["state"],
  operation: "issue" | "verify",
): string {
  if (state === "not_ready") {
    return operation === "issue"
      ? "Origin verification is not configured in this deployment."
      : "The exact proof could not be reached. Check the file and try again.";
  }
  if (state === "rejected") {
    return "The request was rejected. Confirm owner access and the exact site origin.";
  }
  if (state === "conflict") {
    return operation === "issue"
      ? "The challenge state changed. Refresh the page before issuing another proof."
      : "The proof did not verify or the challenge changed. Check both exact values.";
  }
  return "The verification response was invalid. No authority was granted.";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}
