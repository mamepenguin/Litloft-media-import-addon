"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2, Rss } from "lucide-react";

import { useWebSocket } from "@/hooks/useWebSocket";

import {
  listActivity,
  type ActivityEntry,
} from "./api";

interface Props {
  drive: string;
  refreshKey: number;
}

/**
 * Unified import-activity feed. One row per .loft on the drive (single
 * imports + subscription imports together), grouped by day, ordered
 * by created_at DESC. SourceBadge tells the user where the row came
 * from without scattering that information across two lists.
 */

function trimLoftExt(name: string): string {
  return name.endsWith(".loft") ? name.slice(0, -".loft".length) : name;
}

function formatDayHeading(
  d: Date,
  t: (key: string) => string,
): string {
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(d, today)) return t("today");
  if (sameDay(d, yesterday)) return t("yesterday");
  return d.toLocaleDateString();
}

function groupByDay(
  entries: ActivityEntry[],
  t: (key: string) => string,
): { day: string; items: ActivityEntry[] }[] {
  const buckets: Record<string, ActivityEntry[]> = {};
  const order: string[] = [];
  for (const e of entries) {
    const d = new Date(e.created_at);
    const key = formatDayHeading(d, t);
    if (!buckets[key]) {
      buckets[key] = [];
      order.push(key);
    }
    buckets[key].push(e);
  }
  return order.map((day) => ({ day, items: buckets[day] }));
}

export default function ActivityFeed({ drive, refreshKey }: Props) {
  const t = useTranslations("mediaImport.activity");
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  // Core derives this from the `files.updated` emit; the addon no longer
  // broadcasts a raw `files.updated` of its own. The payload carries the
  // drive, which is all this feed reads from it.
  const filesUpdated = useWebSocket("drive.file_updated");

  async function load() {
    if (!drive) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listActivity(drive, 50);
      setEntries(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drive, refreshKey]);

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
        {t("loading")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
        {error}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div
        className="rounded-xl border border-dashed border-bg-border px-4 py-8 text-center text-sm text-text-muted"
        data-testid="activity-empty"
      >
        {t("empty")}
      </div>
    );
  }

  const groups = groupByDay(entries, t);

  return (
    <div data-testid="activity-feed" className="space-y-5">
      {groups.map(({ day, items }) => (
        <section key={day}>
          <h3 className="mb-2 text-[11px] font-semibold uppercase text-text-muted">
            {day}
          </h3>
          <ul className="divide-y divide-bg-border rounded-xl border border-bg-border bg-bg-card">
            {items.map((e) => (
              <li
                key={e.file_id}
                className="flex items-center gap-3 px-4 py-2.5"
                data-testid={`activity-row-${e.file_id}`}
              >
                <button
                  type="button"
                  onClick={() => router.push(`/files/${e.file_id}`)}
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
                >
                  <div className="size-12 shrink-0 overflow-hidden rounded-lg bg-bg-elevated">
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
                      {trimLoftExt(e.filename)}
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
  const t = useTranslations("mediaImport.activity.source");
  if (entry.source === "subscription") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent"
        data-testid="source-badge-subscription"
      >
        <Rss size={10} />
        {entry.subscription_title || t("subscription")}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded-full bg-bg-elevated px-2 py-0.5 text-[10px] font-medium text-text-muted"
      data-testid="source-badge-single"
    >
      {t("single")}
    </span>
  );
}
