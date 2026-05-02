"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useWebSocket } from "@/hooks/useWebSocket";
import {
  listSubscriptionVideos,
  retrySubscriptionVideo,
  type SubscriptionVideo,
} from "./api";

interface Props {
  drive: string;
  subscriptionId: number;
}

const STATUS_LABELS: Record<SubscriptionVideo["status"], string> = {
  imported: "Imported",
  failed: "Failed",
  pending: "Pending",
};

export default function SubscriptionItems({ drive, subscriptionId }: Props) {
  const [videos, setVideos] = useState<SubscriptionVideo[]>([]);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const v = await listSubscriptionVideos(drive, subscriptionId);
      setVideos(v);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load items");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscriptionId]);

  // Refresh the items list when this subscription completes a sync,
  // so retried items flip from "failed" to "imported" without the user
  // having to collapse + re-expand the row.
  const completedEvent = useWebSocket("media_import.subscription.sync_completed");
  useEffect(() => {
    if (!completedEvent) return;
    if (completedEvent.data?.subscription_id !== subscriptionId) return;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedEvent, subscriptionId]);

  async function handleRetry(itemId: string) {
    setRetrying((prev) => new Set(prev).add(itemId));
    setError(null);
    try {
      await retrySubscriptionVideo(drive, subscriptionId, itemId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retry failed");
    } finally {
      setRetrying((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  if (loading) {
    return (
      <div
        className="px-4 py-3 text-sm text-text-muted"
        data-testid="subscription-items-loading"
      >
        Loading items...
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-3 text-sm text-danger">{error}</div>
    );
  }

  if (videos.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-text-muted">
        No items yet. Run a sync.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-border-primary">
      {videos.map((v) => (
        <li
          key={v.item_id}
          className="flex items-center gap-3 px-4 py-2 text-sm"
          data-testid={`video-row-${v.item_id}`}
        >
          <span
            className={
              "rounded px-2 py-0.5 text-xs font-medium " +
              (v.status === "imported"
                ? "bg-success/10 text-success"
                : v.status === "failed"
                  ? "bg-danger/10 text-danger"
                  : "bg-warning/10 text-warning")
            }
          >
            {STATUS_LABELS[v.status]}
          </span>
          <span className="truncate text-text-primary">{v.item_id}</span>
          {v.error_kind && (
            <span className="text-xs text-text-muted">
              ({v.error_kind})
            </span>
          )}
          <span className="ml-auto" />
          {v.status === "failed" && v.error_kind !== "permanent" && (
            <button
              onClick={() => handleRetry(v.item_id)}
              disabled={retrying.has(v.item_id)}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
              data-testid={`retry-${v.item_id}`}
            >
              <RefreshCw
                size={12}
                className={retrying.has(v.item_id) ? "animate-spin" : ""}
              />
              Retry
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
