"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Pause } from "lucide-react";

import { useWebSocket } from "@/hooks/useWebSocket";

import {
  getSubscriptionSummary,
  listSubscriptionVideos,
  listSubscriptions,
  type Subscription,
  type SubscriptionSummary,
  type SubscriptionVideo,
} from "./api";
import SubscriptionCard from "./SubscriptionCard";
import SubscriptionDetailPanel from "./SubscriptionDetailPanel";

type Filter = "all" | "channel" | "playlist" | "attention" | "paused";

interface Props {
  drive: string;
  /** Bumped by the parent (Composer) when a new subscription was created. */
  refreshKey: number;
}

const FILTER_LABELS: Record<Filter, string> = {
  all: "All",
  channel: "Channels",
  playlist: "Playlists",
  attention: "Needs attention",
  paused: "Paused",
};

/**
 * Subscriptions dashboard: summary header + filter chips + cards +
 * detail side panel.
 *
 * The summary numbers come from the dedicated /summary endpoint
 * (one DB scan with the failure counts) so the header strip stays
 * cheap. Per-card imported/failed counts come from a per-id roll-up
 * over subscription_videos rows, refetched with the list.
 */

export default function SubscriptionsDashboard({
  drive,
  refreshKey,
}: Props) {
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [counts, setCounts] = useState<
    Record<number, { imported: number; failed: number }>
  >({});
  const [summary, setSummary] = useState<SubscriptionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const startedEvent = useWebSocket(
    "media_import.subscription.sync_started",
  );
  const completedEvent = useWebSocket(
    "media_import.subscription.sync_completed",
  );

  const load = useCallback(async () => {
    if (!drive) return;
    setLoading(true);
    setError(null);
    try {
      const [rows, sum] = await Promise.all([
        listSubscriptions(drive),
        getSubscriptionSummary(drive),
      ]);
      setSubs(rows);
      setSummary(sum);

      // Per-card counts: parallel video fetches. Acceptable for the
      // expected ~tens-of-subscriptions scale; will revisit if the
      // dashboard grows beyond that.
      const entries = await Promise.all(
        rows.map(async (s) => {
          try {
            const videos = await listSubscriptionVideos(drive, s.id);
            return [s.id, summariseVideos(videos)] as const;
          } catch {
            return [s.id, { imported: 0, failed: 0 }] as const;
          }
        }),
      );
      setCounts(Object.fromEntries(entries));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [drive]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // WS-driven incremental updates.
  useEffect(() => {
    if (!startedEvent) return;
    const sid = startedEvent.data?.subscription_id as number | undefined;
    if (typeof sid !== "number") return;
    setSubs((prev) =>
      prev.map((s) => (s.id === sid ? { ...s, running: true } : s)),
    );
  }, [startedEvent]);

  useEffect(() => {
    if (!completedEvent) return;
    const sid = completedEvent.data?.subscription_id as number | undefined;
    if (typeof sid !== "number") return;
    // Refetch list (running flag + cooldown_until + counts may all change).
    load();
  }, [completedEvent, load]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return subs.filter((s) => {
      if (term) {
        const haystack = (
          (s.display_title ?? "") +
          " " +
          s.source_ref
        ).toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      switch (filter) {
        case "channel":
          return s.source_kind === "channel";
        case "playlist":
          return s.source_kind === "playlist";
        case "paused":
          return !s.is_enabled;
        case "attention": {
          const c = counts[s.id];
          return (c?.failed ?? 0) > 0;
        }
        default:
          return true;
      }
    });
  }, [subs, filter, search, counts]);

  const selected = subs.find((s) => s.id === selectedId) ?? null;

  return (
    <section data-testid="subscriptions-dashboard">
      <SummaryHeader summary={summary} onJumpAttention={() => setFilter("attention")} />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {(Object.keys(FILTER_LABELS) as Filter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`rounded-full border px-3 py-1 text-xs ${
              filter === f
                ? "border-accent-cta bg-accent-cta/10 text-accent-cta"
                : "border-border-primary text-text-secondary hover:bg-bg-hover"
            }`}
            data-testid={`filter-${f}`}
          >
            {FILTER_LABELS[f]}
          </button>
        ))}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search subscriptions..."
          className="ml-auto w-56 rounded-lg border border-border-primary bg-bg-primary px-3 py-1 text-xs text-text-primary placeholder:text-text-muted focus:border-accent-cta focus:outline-none"
        />
      </div>

      <div className="mt-3">
        {error && (
          <div className="mb-3 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        {loading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-text-muted" data-testid="dashboard-loading">
            <Loader2 size={14} className="animate-spin" />
            Loading subscriptions...
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState filter={filter} hasAny={subs.length > 0} />
        ) : (
          <ul className="grid gap-2" data-testid="card-grid">
            {filtered.map((s) => (
              <li key={s.id}>
                <SubscriptionCard
                  subscription={s}
                  importedCount={counts[s.id]?.imported ?? 0}
                  failedCount={counts[s.id]?.failed ?? 0}
                  onClick={() => setSelectedId(s.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {selected && (
        <SubscriptionDetailPanel
          drive={drive}
          subscription={selected}
          onClose={() => setSelectedId(null)}
          onChanged={(updated) => {
            if (updated === null) {
              // Deleted.
              setSubs((prev) => prev.filter((s) => s.id !== selectedId));
              setSelectedId(null);
              load();
            } else {
              setSubs((prev) =>
                prev.map((s) => (s.id === updated.id ? updated : s)),
              );
            }
          }}
        />
      )}
    </section>
  );
}

function summariseVideos(videos: SubscriptionVideo[]): {
  imported: number;
  failed: number;
} {
  let imported = 0;
  let failed = 0;
  for (const v of videos) {
    if (v.status === "imported") imported++;
    else if (v.status === "failed" && v.error_kind !== "dismissed") failed++;
  }
  return { imported, failed };
}

function SummaryHeader({
  summary,
  onJumpAttention,
}: {
  summary: SubscriptionSummary | null;
  onJumpAttention: () => void;
}) {
  if (!summary || summary.total === 0) {
    return null;
  }
  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-lg border border-border-primary bg-bg-card px-4 py-3 text-sm"
      data-testid="dashboard-summary"
    >
      <span className="font-medium text-text-primary">
        {summary.total} subscription{summary.total === 1 ? "" : "s"}
      </span>
      <span className="flex items-center gap-1 text-text-muted">
        <CheckCircle2 size={14} className="text-success" />
        {summary.imported_count} imported
      </span>
      {summary.attention > 0 && (
        <button
          type="button"
          onClick={onJumpAttention}
          className="flex items-center gap-1 text-warning hover:underline"
          data-testid="summary-attention"
        >
          <AlertTriangle size={14} />
          {summary.attention} need{summary.attention === 1 ? "s" : ""} attention
        </button>
      )}
      {summary.paused > 0 && (
        <span className="flex items-center gap-1 text-text-muted">
          <Pause size={14} />
          {summary.paused} paused
        </span>
      )}
      {summary.syncing > 0 && (
        <span className="flex items-center gap-1 text-accent-cta">
          <Loader2 size={14} className="animate-spin" />
          {summary.syncing} syncing
        </span>
      )}
    </div>
  );
}

function EmptyState({
  filter,
  hasAny,
}: {
  filter: Filter;
  hasAny: boolean;
}) {
  if (!hasAny) {
    return (
      <div
        className="rounded-lg border border-dashed border-border-primary px-4 py-8 text-center text-sm text-text-muted"
        data-testid="dashboard-empty"
      >
        No subscriptions yet. Paste a YouTube channel or playlist URL above to start tracking it.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-dashed border-border-primary px-4 py-6 text-center text-sm text-text-muted">
      No subscriptions match this filter ({FILTER_LABELS[filter]}).
    </div>
  );
}
