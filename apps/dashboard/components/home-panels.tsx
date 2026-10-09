import { CircleCheck } from "lucide-react";

import type { HealthCheck } from "../lib/health-api";
import type { GithubState, GithubPrState, GscState } from "../lib/owner-connectors-api";
import type { SlackState } from "../lib/slack-api";
import type { StandingAuthorizationState } from "../lib/standing-authorization-api";
import type { TelegramState } from "../lib/telegram-api";

/* Shared labels for the owner views and the Home setup checklist. Every label
   turns an internal code into plain words; nothing here invents a value. */

export const STAGE_LABELS: Record<string, string> = {
  observe: "Observe",
  analyze: "Analyze",
  plan: "Plan",
  prepare: "Prepare",
  gate: "Check",
  handoff: "Hand off",
  verify: "Verify",
  measure: "Measure",
  report: "Report",
};

export const OUTCOME_LABELS: Record<string, string> = {
  completed: "Done",
  waiting_owner: "Waiting on you",
  deferred: "Deferred",
  unavailable: "Unavailable",
  stopped: "Stopped",
  failed: "Failed",
};

export const REPORT_STATUS: Record<string, string> = {
  running: "Running now",
  completed: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

export const HEALTH_LABELS: Record<HealthCheck["check"], string> = {
  temporal: "Workflow engine",
  weekly_schedule: "Weekly schedule",
  measurement_schedule: "Measurement schedule",
  outbox_backlog: "Outgoing work backlog",
  outbox_age: "Outgoing work age",
  binding_gsc: "Search Console",
  binding_bing: "Bing Webmaster Tools",
  binding_ga4: "Google Analytics",
  binding_github: "GitHub",
  binding_slack: "Slack",
  binding_telegram: "Telegram",
  binding_email: "Email",
  egress_pins: "Network allow-list",
  openbao: "Secret store",
  disk: "Disk space",
  database_size: "Database size",
  write_intents: "Pending writes",
  budget_model: "AI budget",
  budget_dataforseo: "Keyword data budget",
  budget_assistants: "AI answers budget",
};

/** Turns an internal reason code such as OWNER_DECISION_REQUIRED into plain words. */
export function readableCode(code: string): string {
  const words = code.replace(/^EC_\d+_/, "").replace(/[_\s]+/g, " ").trim().toLowerCase();
  return words === "" ? "" : words.charAt(0).toUpperCase() + words.slice(1);
}

export function formatDay(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "an unknown date";
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
}

type StepState = "done" | "todo" | "attention" | "unknown";
const STEP_WORDS: Record<StepState, string> = { done: "Done", todo: "To do", attention: "Needs attention", unknown: "Could not check" };

/** Onboarding for an owner, read only from connection states the page already loaded.
    A state that could not be read is "Could not check", never "To do" or "Done". */
export function SetupChecklist({
  verified,
  gsc,
  github,
  githubPr,
  slack,
  telegram,
  standing,
}: {
  verified: boolean;
  gsc: GscState;
  github: GithubState;
  githubPr: GithubPrState;
  slack: SlackState;
  telegram: TelegramState;
  standing: StandingAuthorizationState;
}) {
  const phone: StepState =
    slack.availability === "bound" || telegram.availability === "bound" ? "done"
      : slack.availability === "unavailable" && telegram.availability === "unavailable" ? "unknown" : "todo";
  const steps: { label: string; detail: string; href: string; state: StepState; optional?: boolean }[] = [
    { label: "Verify you own the site", detail: "Proves Signal is working on your site, not someone else’s.", href: "#site-directory-title", state: verified ? "done" : "todo" },
    {
      label: "Connect Search Console", detail: "Read-only search data Signal learns from.", href: "/connectors",
      state: gsc.availability === "bound" ? "done" : gsc.availability === "unavailable" ? "unknown" : gsc.availability === "unbound" ? "todo" : "attention",
    },
    {
      label: "Connect GitHub", detail: "The repository Signal opens pull requests against.", href: "/connectors",
      state: github.availability === "active" ? "done" : github.availability === "unavailable" ? "unknown" : github.availability === "unbound" ? "todo" : "attention",
    },
    { label: "Approve from your phone", detail: "Slack or Telegram, for decisions and weekly reports.", href: "/connectors", state: phone, optional: true },
    {
      label: "Let Signal open pull requests", detail: "Separate permission to propose reviewed repository changes.", href: "/connectors#github-title", optional: true,
      state: githubPr.availability === "observed" ? "done" : githubPr.availability === "unavailable" ? "unknown" : githubPr.availability === "ungranted" ? "todo" : "attention",
    },
    {
      label: "Let Signal work on its own", detail: "A weekly allowance for small changes, within your caps.", href: "/policy", optional: true,
      state: standing.state !== "available" ? "unknown" : standing.grant.state === "active" ? "done" : "todo",
    },
  ];
  const required = steps.filter((step) => !step.optional);
  if (required.every((step) => step.state === "done")) return null;
  const done = steps.filter((step) => step.state === "done").length;
  return (
    <section className="setup-checklist" aria-labelledby="setup-title">
      <header>
        <h2 id="setup-title">Get Signal working</h2>
        <span className="pill">{done} of {steps.length} done</span>
      </header>
      <ol>
        {steps.map((step) => (
          <li key={step.label} data-state={step.state}>
            <span className="setup-mark" aria-hidden="true">{step.state === "done" ? <CircleCheck size={18} /> : null}</span>
            <div>
              <a href={step.href}>{step.label}</a>
              {step.optional ? <small> · Optional</small> : null}
              <p>{step.detail}</p>
            </div>
            <span className="setup-state">{STEP_WORDS[step.state]}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
