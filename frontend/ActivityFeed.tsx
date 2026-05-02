"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Rss } from "lucide-react";

import { useWebSocket } from "@/hooks/useWebSocket";

import {
  listActivity,
  type ActivityEntry,
} from "./api";

interface Props {
  drive: string;
  /** Bumped by the parent on Composer success or sync completion. */
  refreshKey: number;
}

/**
 * Unified import-activity feed for the bottom of the dashboard.
 *
 * One row per loft file on the drive (single imports + subscription
 * imports together), grouped by day, ordered created_at-DESC. The
 * source badge tells the user "this came in via subscription X" vs
 * "you pasted this manually" without scattering the information.
 *
 * Refetches when the parent bumps refreshKey (new compose / new
 * subscription) or when files.updated lands over the WebSocket.
 */

function formatDayHeading(d: Date): string {
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(d, today)) return "Today";
  if (sameDay(d, yesterday)) return "Yesterday";
  return d.toLocaleDateString();
}

function groupByDay(
  entries: ActivityEntry[],
): { day: string; items: ActivityEntry[] }[] {
  const buckets: Record<string, ActivityEntry[]> = {};
  for (const e of entries) {
    const d = new Date(e.created_at);
    const key = formatDayHeading(d);
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(e);
  }
  return Object.entries(buckets).map(([day, items]) => ({ day, items }));
}

export default function ActivityFeed({ drive, refreshKey }: Props) {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const filesUpdated = useWebSocket("files.updated");

  async function load() {
    if (!drive) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listActivity(drive, 50);
      setEntries(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load activity");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drive, refreshKey]);

  // Files-updated events on this drive are rare enough that a full
  // refetch is fine.
  useEffect(() => {
    if (!filesUpdated) return;
    if (filesUpdated.data?.drive !== drive) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filesUpdated, drive]);

  if (loading) {
    return (
      <div
        className="flex items-center gap-2 py-4 text-sm text-text-muted"
        data-testid="activity-loading"
      >
        <Loader2 size={14} className="animate-spin" />
        Loading recent activity...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
        {error}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div
        className="rounded-lg border border-dashed border-border-primary px-4 py-6 text-center text-sm text-text-muted"
        data-testid="activity-empty"
      >
        No imports on this drive yet.
      </div>
    );
  }

  const groups = groupByDay(entries);

  return (
    <div data-testid="activity-feed" className="space-y-4">
      {groups.map(({ day, items }) => (
        <section key={day}>
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">
            {day}
          </h3>
          <ul className="divide-y divide-border-primary rounded-lg border border-border-primary bg-bg-card">
            {items.map((e) => (
              <li
                key={e.file_id}
                className="flex items-center gap-3 px-3 py-2"
                data-testid={`activity-row-${e.file_id}`}
              >
                <button
                  type="button"
                  onClick={() => router.push(`/files/${e.file_id}`)}
                  className="flex flex-1 items-center gap-3 text-left"
                >
                  <div className="size-12 shrink-0 overflow-hidden rounded bg-bg-hover">
                    {e.thumbnail_path && (
                      <img
                        src={`/api/files/${e.file_id}/thumbnail`}
                        alt=""
                        className="size-full object-cover"
                        onError={(ev) => {
                          (ev.target as HTMLImageElement).style.display = "none";
                        }}
                      />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-text-primary">
                      {e.filename}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-text-muted">
                      <SourceBadge entry={e} />
                      {e.channel && (
                        <>
                          <span>·</span>
                          <span className="truncate">{e.channel}</span>
                        </>
                      )}
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function SourceBadge({ entry }: { entry: ActivityEntry }) {
  if (entry.source === "subscription") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded bg-accent-cta/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-cta"
        data-testid="source-badge-subscription"
      >
        <Rss size={10} />
        {entry.subscription_title || "Subscription"}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded bg-bg-hover px-1.5 py-0.5 text-[10px] font-medium text-text-muted"
      data-testid="source-badge-single"
    >
      Single import
    </span>
  );
}
