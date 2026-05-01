"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw, Trash2 } from "lucide-react";
import {
  deleteSubscription,
  listSubscriptions,
  syncSubscription,
  type Subscription,
  type SubscriptionSyncResult,
} from "./api";
import SubscriptionItems from "./SubscriptionItems";

interface Props {
  drive: string;
}

function formatLastSynced(iso: string | null): string {
  if (!iso) return "Never";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function kindBadge(kind: string): string {
  return kind === "channel"
    ? "Channel"
    : kind === "playlist"
      ? "Playlist"
      : kind === "feed"
        ? "Feed"
        : kind;
}

export default function SubscriptionsList({ drive }: Props) {
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [lastSummary, setLastSummary] = useState<
    Record<number, SubscriptionSyncResult>
  >({});

  const load = useCallback(async () => {
    if (!drive) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listSubscriptions(drive);
      setSubs(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [drive]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSync(id: number) {
    setBusyId(id);
    setError(null);
    try {
      const result = await syncSubscription(id);
      setLastSummary((prev) => ({ ...prev, [id]: result }));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this subscription? This cannot be undone.")) return;
    setBusyId(id);
    setError(null);
    try {
      await deleteSubscription(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusyId(null);
    }
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (loading) {
    return (
      <div
        className="text-sm text-text-muted"
        data-testid="subscriptions-loading"
      >
        Loading subscriptions...
      </div>
    );
  }

  if (subs.length === 0 && !error) {
    return (
      <div
        className="text-sm text-text-muted"
        data-testid="subscriptions-empty"
      >
        No subscriptions on this drive yet.
      </div>
    );
  }

  return (
    <div data-testid="subscriptions-list">
      {error && (
        <div className="mb-3 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}

      <ul className="space-y-2">
        {subs.map((s) => {
          const isExpanded = expanded.has(s.id);
          const summary = lastSummary[s.id];
          return (
            <li
              key={s.id}
              className="rounded-lg border border-border-primary bg-bg-card"
              data-testid={`subscription-row-${s.id}`}
            >
              <div className="flex items-center gap-3 px-3 py-2">
                <button
                  onClick={() => toggleExpand(s.id)}
                  className="text-text-muted hover:text-text-primary"
                  aria-label="Toggle items"
                >
                  {isExpanded ? (
                    <ChevronDown size={16} />
                  ) : (
                    <ChevronRight size={16} />
                  )}
                </button>
                <div className="flex flex-1 flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary">
                      {s.title || s.source_ref}
                    </span>
                    <span className="rounded bg-bg-hover px-1.5 py-0.5 text-xs text-text-secondary">
                      {kindBadge(s.source_kind)}
                    </span>
                    {s.folder_path && (
                      <span className="text-xs text-text-muted">
                        / {s.folder_path}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-text-muted">
                    Last synced: {formatLastSynced(s.last_synced_at)}
                    {summary && (
                      <span className="ml-2">
                        (+{summary.added} added, {summary.reused} reused
                        {summary.failed > 0
                          ? `, ${summary.failed} failed`
                          : ""}
                        )
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => handleSync(s.id)}
                  disabled={busyId === s.id}
                  className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                  data-testid={`sync-${s.id}`}
                >
                  <RefreshCw
                    size={12}
                    className={busyId === s.id ? "animate-spin" : ""}
                  />
                  Sync
                </button>
                <button
                  onClick={() => handleDelete(s.id)}
                  disabled={busyId === s.id}
                  className="flex items-center gap-1 rounded px-2 py-1 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
                  data-testid={`delete-${s.id}`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
              {isExpanded && (
                <div className="border-t border-border-primary">
                  <SubscriptionItems subscriptionId={s.id} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
