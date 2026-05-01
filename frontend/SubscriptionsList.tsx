"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw, Trash2 } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  deleteSubscription,
  listSubscriptions,
  syncSubscription,
  type Subscription,
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
  // Track which subscriptions are syncing on top of the snapshot from
  // GET /subscriptions. Keys are added on sync_started, removed on
  // sync_completed; the next refetch reconciles with server state.
  const [syncing, setSyncing] = useState<Set<number>>(new Set());

  const startedEvent = useWebSocket("media_import.subscription.sync_started");
  const completedEvent = useWebSocket("media_import.subscription.sync_completed");

  const load = useCallback(async () => {
    if (!drive) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listSubscriptions(drive);
      setSubs(rows);
      // Reconcile syncing set with server-side running flag.
      setSyncing(new Set(rows.filter((s) => s.running).map((s) => s.id)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [drive]);

  useEffect(() => {
    load();
  }, [load]);

  // Apply WS sync_started events optimistically.
  useEffect(() => {
    if (!startedEvent) return;
    const sid = startedEvent.data?.subscription_id as number | undefined;
    if (typeof sid !== "number") return;
    setSyncing((prev) => {
      if (prev.has(sid)) return prev;
      const next = new Set(prev);
      next.add(sid);
      return next;
    });
  }, [startedEvent]);

  // On sync_completed, drop the syncing flag and refetch so summaries
  // (added/reused/failed counts on subscription rows) refresh.
  useEffect(() => {
    if (!completedEvent) return;
    const sid = completedEvent.data?.subscription_id as number | undefined;
    if (typeof sid !== "number") return;
    setSyncing((prev) => {
      if (!prev.has(sid)) return prev;
      const next = new Set(prev);
      next.delete(sid);
      return next;
    });
    load();
  }, [completedEvent, load]);

  async function handleSync(id: number) {
    setBusyId(id);
    setError(null);
    try {
      await syncSubscription(id);
      // Worker will broadcast sync_started shortly; mark optimistically
      // so the badge appears even if the WS event lands a tick later.
      setSyncing((prev) => {
        const next = new Set(prev);
        next.add(id);
        return next;
      });
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
          const isSyncing = syncing.has(s.id);
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
                    {isSyncing && (
                      <span
                        className="rounded bg-accent-cta/10 px-1.5 py-0.5 text-xs text-accent-cta"
                        data-testid={`syncing-badge-${s.id}`}
                      >
                        Syncing…
                      </span>
                    )}
                    {s.folder_path && (
                      <span className="text-xs text-text-muted">
                        / {s.folder_path}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-text-muted">
                    Last synced: {formatLastSynced(s.last_synced_at)}
                  </div>
                </div>
                <button
                  onClick={() => handleSync(s.id)}
                  disabled={busyId === s.id || isSyncing}
                  className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                  data-testid={`sync-${s.id}`}
                >
                  <RefreshCw
                    size={12}
                    className={isSyncing ? "animate-spin" : ""}
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
