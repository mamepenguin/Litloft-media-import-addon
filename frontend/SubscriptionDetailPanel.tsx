"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw, Trash2, X } from "lucide-react";

import { useWebSocket } from "@/hooks/useWebSocket";

import {
  deleteSubscription,
  listSubscriptionVideos,
  patchSubscription,
  refreshSubscriptionMetadata,
  retrySubscriptionVideo,
  subscriptionAvatarUrl,
  syncSubscription,
  type Subscription,
  type SubscriptionVideo,
} from "./api";
import ConflictResolveDialog from "./ConflictResolveDialog";
import SubscriptionItemRow from "./SubscriptionItemRow";
import SubscriptionStatusPill, {
  deriveStatus,
} from "./SubscriptionStatusPill";

interface Props {
  drive: string;
  subscription: Subscription;
  onClose: () => void;
  onChanged: (sub: Subscription | null) => void;
}

/**
 * Right-slide panel showing one subscription's full state.
 *
 * Sections:
 *   header   — avatar / display title / status pill / close
 *   actions  — Pause/Resume, Sync now, Refresh metadata
 *   schedule — cooldown_minutes editor + next-sync hint
 *   options  — include_no_transcript checkbox
 *   items    — failed first (grouped), then imported (recent)
 *   danger   — delete with item-count warning
 *
 * Items are refetched on ``subscription.sync_completed`` events for
 * this id, so retries and conflict resolutions reflect without
 * collapsing+reopening the panel.
 */

function nextSyncHint(sub: Subscription): string {
  if (!sub.is_enabled) return "Paused — automatic sync disabled";
  if (sub.cooldown_until) {
    const cooldown = new Date(sub.cooldown_until);
    if (cooldown > new Date()) {
      return `Backoff until ${cooldown.toLocaleString()}`;
    }
  }
  if (!sub.last_synced_at) return "Will sync on the next cron sweep";
  const last = new Date(sub.last_synced_at);
  const next = new Date(last.getTime() + sub.cooldown_minutes * 60_000);
  return `Next sync around ${next.toLocaleString()}`;
}

export default function SubscriptionDetailPanel({
  drive,
  subscription: initial,
  onClose,
  onChanged,
}: Props) {
  const [subscription, setSubscription] = useState(initial);
  const [videos, setVideos] = useState<SubscriptionVideo[]>([]);
  const [loadingVideos, setLoadingVideos] = useState(true);
  const [retrying, setRetrying] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictItem, setConflictItem] = useState<string | null>(null);
  const [editingCooldown, setEditingCooldown] = useState(
    subscription.cooldown_minutes,
  );

  const completedEvent = useWebSocket(
    "media_import.subscription.sync_completed",
  );

  async function loadVideos() {
    setLoadingVideos(true);
    try {
      const v = await listSubscriptionVideos(drive, subscription.id);
      setVideos(v);
    } finally {
      setLoadingVideos(false);
    }
  }

  useEffect(() => {
    setSubscription(initial);
    setEditingCooldown(initial.cooldown_minutes);
  }, [initial]);

  useEffect(() => {
    loadVideos();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subscription.id]);

  useEffect(() => {
    if (!completedEvent) return;
    if (completedEvent.data?.subscription_id !== subscription.id) return;
    loadVideos();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedEvent, subscription.id]);

  async function applyPatch(
    patch: Parameters<typeof patchSubscription>[2],
  ): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const updated = await patchSubscription(drive, subscription.id, patch);
      setSubscription(updated);
      onChanged(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleSync() {
    setBusy(true);
    setError(null);
    try {
      await syncSubscription(drive, subscription.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshMetadata() {
    setBusy(true);
    setError(null);
    try {
      await refreshSubscriptionMetadata(drive, subscription.id);
      // The avatar URL on the server may have changed; force <img> re-fetch.
      setSubscription((s) => ({ ...s }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    const importedCount = videos.filter((v) => v.status === "imported").length;
    const message =
      `Delete "${subscription.display_title || subscription.source_ref}" ` +
      `subscription? ${importedCount} imported file${importedCount === 1 ? "" : "s"} ` +
      `will stay; new uploads will stop being tracked.`;
    if (!confirm(message)) return;

    setBusy(true);
    setError(null);
    try {
      await deleteSubscription(drive, subscription.id);
      onChanged(null);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setBusy(false);
    }
  }

  async function handleRetry(itemId: string) {
    setRetrying((prev) => new Set(prev).add(itemId));
    try {
      await retrySubscriptionVideo(drive, subscription.id, itemId);
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

  // Group failed first, then imported.
  const failed = videos.filter(
    (v) => v.status === "failed" && v.error_kind !== "dismissed",
  );
  const imported = videos.filter((v) => v.status === "imported");
  const dismissed = videos.filter(
    (v) => v.status === "failed" && v.error_kind === "dismissed",
  );

  const status = deriveStatus(subscription, failed.length);
  const importedCount = imported.length;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={onClose}
      data-testid="detail-panel"
    >
      <aside
        className="h-full w-full max-w-md overflow-y-auto bg-bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border-primary px-5 py-4">
          <div className="flex items-start gap-3 min-w-0">
            <div className="size-12 shrink-0 overflow-hidden rounded-full bg-bg-hover">
              {subscription.avatar_url && (
                <img
                  src={subscriptionAvatarUrl(subscription.id)}
                  alt=""
                  className="size-full object-cover"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
              )}
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-text-primary">
                {subscription.display_title || subscription.source_ref}
              </h2>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-text-secondary">
                <span>{subscription.provider}</span>
                <span>·</span>
                <span>{subscription.source_kind}</span>
                <span>·</span>
                <span>{importedCount} imported</span>
              </div>
              <div className="mt-2">
                <SubscriptionStatusPill status={status} />
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-text-muted hover:text-text-primary"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </header>

        <section className="border-b border-border-primary px-5 py-4">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => applyPatch({ is_enabled: !subscription.is_enabled })}
              disabled={busy}
              className="flex items-center gap-1 rounded-lg border border-border-primary px-3 py-1.5 text-xs text-text-primary hover:bg-bg-hover disabled:opacity-50"
              data-testid="action-pause"
            >
              {subscription.is_enabled ? "Pause" : "Resume"}
            </button>
            <button
              type="button"
              onClick={handleSync}
              disabled={busy || subscription.running}
              className="flex items-center gap-1 rounded-lg border border-border-primary px-3 py-1.5 text-xs text-text-primary hover:bg-bg-hover disabled:opacity-50"
              data-testid="action-sync"
            >
              <RefreshCw size={12} className={subscription.running ? "animate-spin" : ""} />
              Sync now
            </button>
            <button
              type="button"
              onClick={handleRefreshMetadata}
              disabled={busy}
              className="flex items-center gap-1 rounded-lg border border-border-primary px-3 py-1.5 text-xs text-text-primary hover:bg-bg-hover disabled:opacity-50"
              data-testid="action-refresh-metadata"
            >
              Refresh metadata
            </button>
          </div>
          {error && (
            <div
              className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-xs text-danger"
              data-testid="panel-error"
            >
              {error}
            </div>
          )}
        </section>

        <section className="border-b border-border-primary px-5 py-4">
          <h3 className="text-xs font-medium text-text-secondary">Schedule</h3>
          <p className="mt-1 text-xs text-text-muted">{nextSyncHint(subscription)}</p>
          <div className="mt-3 flex items-center gap-2">
            <label className="text-xs text-text-muted">
              Cooldown (min):
            </label>
            <input
              type="number"
              min={1}
              value={editingCooldown}
              onChange={(e) =>
                setEditingCooldown(Math.max(1, Number(e.target.value) || 1))
              }
              className="w-20 rounded-lg border border-border-primary bg-bg-primary px-2 py-1 text-xs text-text-primary focus:border-accent-cta focus:outline-none"
              data-testid="cooldown-input"
            />
            <button
              type="button"
              onClick={() =>
                applyPatch({ cooldown_minutes: editingCooldown })
              }
              disabled={busy || editingCooldown === subscription.cooldown_minutes}
              className="rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
              data-testid="cooldown-save"
            >
              Save
            </button>
          </div>
        </section>

        <section className="border-b border-border-primary px-5 py-4">
          <h3 className="text-xs font-medium text-text-secondary">Destination</h3>
          <div className="mt-1 text-xs text-text-primary">
            {subscription.folder_path
              ? `/${subscription.folder_path}`
              : "drive root"}
          </div>
        </section>

        <section className="border-b border-border-primary px-5 py-4">
          <h3 className="text-xs font-medium text-text-secondary">Options</h3>
          <label className="mt-2 flex items-center gap-2 text-xs text-text-primary">
            <input
              type="checkbox"
              checked={subscription.include_no_transcript}
              onChange={(e) =>
                applyPatch({ include_no_transcript: e.target.checked })
              }
              disabled={busy}
              data-testid="include-no-transcript"
            />
            Try to fetch transcripts even when none reported
          </label>
        </section>

        <section className="border-b border-border-primary px-5 py-4">
          <h3 className="mb-2 text-xs font-medium text-text-secondary">Items</h3>
          {loadingVideos ? (
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <Loader2 size={12} className="animate-spin" />
              Loading...
            </div>
          ) : videos.length === 0 ? (
            <div className="text-xs text-text-muted">
              No items yet. Run a sync.
            </div>
          ) : (
            <div className="-mx-5">
              {failed.length > 0 && (
                <div data-testid="items-failed-group">
                  <div className="px-5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-warning">
                    Needs attention ({failed.length})
                  </div>
                  <ul className="divide-y divide-border-primary">
                    {failed.map((v) => (
                      <SubscriptionItemRow
                        key={v.item_id}
                        video={v}
                        retrying={retrying.has(v.item_id)}
                        onRetry={() => handleRetry(v.item_id)}
                        onResolveConflict={() => setConflictItem(v.item_id)}
                      />
                    ))}
                  </ul>
                </div>
              )}
              {imported.length > 0 && (
                <div data-testid="items-imported-group">
                  <div className="mt-3 px-5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                    Imported ({imported.length})
                  </div>
                  <ul className="divide-y divide-border-primary">
                    {imported.slice(0, 50).map((v) => (
                      <SubscriptionItemRow
                        key={v.item_id}
                        video={v}
                        retrying={false}
                        onRetry={() => {}}
                        onResolveConflict={() => {}}
                      />
                    ))}
                  </ul>
                </div>
              )}
              {dismissed.length > 0 && (
                <div data-testid="items-dismissed-group">
                  <div className="mt-3 px-5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                    Skipped ({dismissed.length})
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        <section className="px-5 py-4">
          <h3 className="text-xs font-medium text-danger">Danger zone</h3>
          <button
            type="button"
            onClick={handleDelete}
            disabled={busy}
            className="mt-2 flex items-center gap-1 rounded-lg border border-danger/40 px-3 py-1.5 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
            data-testid="action-delete"
          >
            <Trash2 size={12} />
            Delete subscription
          </button>
        </section>
      </aside>

      {conflictItem && (
        <ConflictResolveDialog
          drive={drive}
          subscriptionId={subscription.id}
          itemId={conflictItem}
          onClose={() => setConflictItem(null)}
          onResolved={() => {
            loadVideos();
          }}
        />
      )}
    </div>
  );
}
