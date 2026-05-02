"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
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

const FILTERS: Filter[] = ["all", "channel", "playlist", "attention", "paused"];

interface Props {
  drive: string;
  /** Bumped by the parent (Composer) when a new subscription was created. */
  refreshKey: number;
}

/**
 * Subscriptions dashboard: summary header + filter chips + cards +
 * detail side panel.
 *
 * Cards live on a responsive grid (1/2/3 columns) so the dashboard
 * scales from phone to wide desktop without forcing a list-scan UX.
 */

export default function SubscriptionsDashboard({
  drive,
  refreshKey,
}: Props) {
  const t = useTranslations("mediaImport.dashboard");
  const tFilter = useTranslations("mediaImport.dashboard.filter");
  const tSummary = useTranslations("mediaImport.dashboard.summary");

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
      setError(e instanceof Error ? e.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [drive, t]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

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
    <section data-testid="subscriptions-dashboard" className="space-y-4">
      <SummaryHeader
        summary={summary}
        onJumpAttention={() => setFilter("attention")}
        tSummary={tSummary}
      />

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              filter === f
                ? "border-accent bg-accent/10 text-accent"
                : "border-bg-border text-text-muted hover:bg-bg-elevated"
            }`}
            data-testid={`filter-${f}`}
          >
            {tFilter(f)}
          </button>
        ))}
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="ml-auto w-56 rounded-2xl border border-bg-border bg-bg-card px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:border-focus-ring focus:outline-none"
        />
      </div>

      <div>
        {error && (
          <div className="mb-3 rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        {loading ? (
          <div
            className="flex items-center gap-2 py-8 text-sm text-text-muted"
            data-testid="dashboard-loading"
          >
            <Loader2 size={14} className="animate-spin" />
            {t("loading")}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState filter={filter} hasAny={subs.length > 0} />
        ) : (
          <ul
            className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
            data-testid="card-grid"
          >
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

type SummaryTranslator = ReturnType<typeof useTranslations<"mediaImport.dashboard.summary">>;

function SummaryHeader({
  summary,
  onJumpAttention,
  tSummary,
}: {
  summary: SubscriptionSummary | null;
  onJumpAttention: () => void;
  tSummary: SummaryTranslator;
}) {
  if (!summary || summary.total === 0) {
    return null;
  }
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-bg-border bg-bg-card px-4 py-3 text-sm"
      data-testid="dashboard-summary"
    >
      <span className="font-semibold text-text-primary">
        {tSummary("subscriptions", { count: summary.total })}
      </span>
      <span className="flex items-center gap-1 text-text-muted">
        <CheckCircle2 size={14} className="text-accent-teal" />
        {tSummary("imported", { count: summary.imported_count })}
      </span>
      {summary.attention > 0 && (
        <button
          type="button"
          onClick={onJumpAttention}
          className="flex items-center gap-1 text-accent-amber hover:underline"
          data-testid="summary-attention"
        >
          <AlertTriangle size={14} />
          {tSummary("attentionPlural", { count: summary.attention })}
        </button>
      )}
      {summary.paused > 0 && (
        <span className="flex items-center gap-1 text-text-muted">
          <Pause size={14} />
          {tSummary("paused", { count: summary.paused })}
        </span>
      )}
      {summary.syncing > 0 && (
        <span className="flex items-center gap-1 text-accent">
          <Loader2 size={14} className="animate-spin" />
          {tSummary("syncing", { count: summary.syncing })}
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
  const t = useTranslations("mediaImport.dashboard");
  const tFilter = useTranslations("mediaImport.dashboard.filter");
  if (!hasAny) {
    return (
      <div
        className="rounded-xl border border-dashed border-bg-border px-4 py-10 text-center text-sm text-text-muted"
        data-testid="dashboard-empty"
      >
        {t("emptyAll")}
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-dashed border-bg-border px-4 py-8 text-center text-sm text-text-muted">
      {t("emptyFiltered", { filter: tFilter(filter) })}
    </div>
  );
}
