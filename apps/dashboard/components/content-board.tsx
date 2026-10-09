import type { WriterData } from "../lib/content-writer-api";
import type { DashboardDeliveryObservations } from "../lib/github-delivery-api";
import type { DashboardGithubPrOperations } from "../lib/github-pr-operations-api";
import type { OwnerInsights } from "../lib/owner-insights";
import type { TopicCluster } from "../lib/seo-strategy-api";
import { readableCode } from "./home-panels";

/* Content: what Signal writes next, as a board of recorded ideas, briefs,
   drafts, reviews and published articles. Search volumes appear only where a
   licensed provider reported them. */

interface Card { title: string; keyword?: string; volume?: string; tag?: { text: string; tone: string }; status?: { text: string; tone: string }; note?: string; review?: string }
interface Column { title: string; live: boolean; cards: Card[]; empty: string }

const GAP_WORDS: Record<string, string> = { high_impressions_low_ctr: "Seen often, rarely clicked", weak_position: "Ranks low", no_targeting_page: "No page targets it", untargeted: "No page targets it" };

function volumeOf(cluster: TopicCluster): string | undefined {
  const values = cluster.members.flatMap((member) => member.volumes).map((volume) => volume.value).filter((value): value is number => typeof value === "number");
  if (values.length === 0) return undefined;
  return `${new Intl.NumberFormat("en").format(Math.max(...values))}/mo`;
}

export function ContentHeader({ writer }: { writer: WriterData | null }) {
  return (
    <header className="c-ph">
      <div>
        <span className="c-ml">Content{writer ? ` · ${writer.used} of ${writer.cap} drafts this week` : ""}</span>
        <h1 className="c-h1"><span className="c-ln"><span>What Signal writes next.</span></span></h1>
      </div>
      <div className="c-acts"><a className="secondary-command" href="/content-writer#new-brief">Suggest a topic</a></div>
    </header>
  );
}

export function ContentBoard({ insights, githubPrOperations, deliveryObservations }: {
  insights: OwnerInsights;
  githubPrOperations: DashboardGithubPrOperations;
  deliveryObservations: DashboardDeliveryObservations;
}) {
  const writer = insights.writer.state === "available" ? insights.writer.value : null;
  const topics = insights.seo.state === "available" ? insights.seo.value.snapshot?.payload.topics ?? null : null;
  const decisions = insights.seo.state === "available" ? insights.seo.value.decisions : [];
  const operations = githubPrOperations.state === "available" ? githubPrOperations.operations.filter((item) => item.authority?.kind === "owner_editorial") : [];
  const observations = deliveryObservations.state === "available" ? deliveryObservations.observations : [];
  const topicOf = (draftId: string) => {
    const draft = writer?.drafts.find((item) => item.draft_id === draftId);
    return writer?.briefs.find((item) => item.brief_id === draft?.brief_id)?.payload;
  };

  const ideas: Card[] = (topics?.clusters ?? [])
    .filter((cluster) => cluster.gaps.length > 0 && !decisions.some((decision) => decision.item_id === cluster.strategy_item_id))
    .slice(0, 6)
    .map((cluster) => ({
      title: cluster.title,
      keyword: cluster.members[0]?.query,
      volume: volumeOf(cluster),
      tag: { text: GAP_WORDS[cluster.gaps[0]] ?? readableCode(cluster.gaps[0]), tone: "c-pill" },
    }));
  const drafted = new Set(writer?.drafts.map((draft) => draft.brief_id) ?? []);
  const briefs: Card[] = (writer?.briefs ?? []).filter((brief) => brief.status === "accepted" && !drafted.has(brief.brief_id)).map((brief) => ({
    title: brief.payload.topic, keyword: brief.payload.query, status: { text: "Approved, waiting to be drafted", tone: "c-pill o" },
  }));
  const reviewing = new Set(writer?.candidates.map((candidate) => candidate.draft_id) ?? []);
  const writing: Card[] = (writer?.drafts ?? []).filter((draft) => !reviewing.has(draft.draft_id)).map((draft) => {
    const brief = writer?.briefs.find((item) => item.brief_id === draft.brief_id)?.payload;
    return { title: brief?.topic ?? "Untitled draft", keyword: brief?.query, note: draft.result.state === "owner_required" ? "Draft has a question for you" : readableCode(draft.result.state), status: draft.result.quality ? { text: draft.result.quality.state === "passed" ? "Quality checks passed" : "Quality needs work", tone: draft.result.quality.state === "passed" ? "c-pill g" : "c-pill w" } : undefined };
  });
  const reviews: Card[] = (writer?.candidates ?? []).filter((candidate) => candidate.review_status === "pending" || (candidate.review_status === "approved" && !candidate.delivery_approval_id)).map((candidate) => ({
    title: topicOf(candidate.draft_id)?.topic ?? candidate.manifest.changed_files[0]?.path ?? "New article",
    keyword: topicOf(candidate.draft_id)?.query,
    review: `/approvals?article=${candidate.candidate_id}`,
  }));
  const published: Card[] = operations.map((operation) => {
    const verified = observations.some((item) => item.operationId === operation.operationId && item.state === "completed" && item.outcome === "verified");
    return { title: operation.prNumber ? `Article, pull request #${operation.prNumber}` : "Article pull request", status: verified ? { text: "Live and verified", tone: "c-pill g" } : { text: "Waiting for your merge", tone: "c-pill w" } };
  });

  const columns: Column[] = [
    { title: "Ideas", live: false, cards: ideas, empty: topics ? "No gaps found in your search data yet." : "Ideas appear once Search Console data is imported." },
    { title: "Brief ready", live: false, cards: briefs, empty: "No approved briefs waiting." },
    { title: "Writing", live: writing.length > 0, cards: writing, empty: "Nothing being written." },
    { title: "Your review", live: false, cards: reviews, empty: "Nothing waits for your review." },
    { title: "Published", live: false, cards: published, empty: "No articles shipped yet." },
  ];
  let delay = 0;
  return (
    <div className="c-board" aria-label="Content board">
      {columns.map((column) => (
        <section className="c-bcol" key={column.title} aria-label={column.title}>
          <div className="c-bcol-h"><span className={column.live ? "c-ldot" : "c-sq"} aria-hidden="true" /><b>{column.title}</b><span className="c-ml">{column.cards.length}</span></div>
          {column.cards.length === 0 ? <p className="c-bcol-empty">{column.empty}</p> : column.cards.map((card, index) => (
            <div className="c-card c-rs" key={`${card.title}:${index}`} style={{ animationDelay: `${(delay++) * 0.04}s` }}>
              <b>{card.title}</b>
              {card.keyword ? <span className="c-kw">“{card.keyword}”{card.volume ? ` · ${card.volume}` : ""}</span> : null}
              {card.note ? <span className="c-card-note">{card.note}</span> : null}
              {card.tag || card.status ? <div className="c-tags">{card.tag ? <span className={card.tag.tone}>{card.tag.text}</span> : null}{card.status ? <span className={card.status.tone}>{card.status.text}</span> : null}</div> : null}
              {card.review ? <a className="primary-command c-card-btn" href={card.review}>Review</a> : null}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
