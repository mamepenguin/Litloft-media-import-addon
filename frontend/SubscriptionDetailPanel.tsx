"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2, Pencil, RefreshCw, Trash2, X } from "lucide-react";

import { useWebSocket } from "@/hooks/useWebSocket";

import {
  deleteSubscription,
  dismissSubscriptionVideo,
  extendBackfill,
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
import DisplayModeField from "./DisplayModeField";
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
 * Layout follows DESIGN.md §6 sidebar conventions:
 *   - rounded-2xl, bg-bg-card, divided by border-bg-border
 *   - sticky header with avatar + display title + status pill
 *   - sections separated by ``border-t border-bg-border``
 *
 * Items are refetched on ``subscription.sync_completed`` events for
 * this id, so retries and conflict resolutions reflect without
 * collapsing+reopening the panel.
 */

export default function SubscriptionDetailPanel({
  drive,
  subscription: initial,
  onClose,
  onChanged,
}: Props) {
  const t = useTranslations("mediaImport.detail");

  const [subscription, setSubscription] = useState(initial);
  const [videos, setVideos] = useState<SubscriptionVideo[]>([]);
  const [loadingVideos, setLoadingVideos] = useState(true);
  const [retrying, setRetrying] = useState<Set<string>>(new Set());
  const [dismissing, setDismissing] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictItem, setConflictItem] = useState<string | null>(null);
  const [editingCooldown, setEditingCooldown] = useState(
    subscription.cooldown_minutes,
  );
  const [editingFolder, setEditingFolder] = useState(false);
  const [folderInput, setFolderInput] = useState(subscription.folder_path);
  const [backfillCount, setBackfillCount] = useState(15);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillMessage, setBackfillMessage] = useState<string | null>(null);

  const completedEvent = useWebSocket(
    "media_import.subscription.sync_completed",
  );

  function nextSyncHint(sub: Subscription): string {
    if (!sub.is_enabled) return t("schedule.paused");
    if (sub.cooldown_until) {
      const cooldown = new Date(sub.cooldown_until);
      if (cooldown > new Date()) {
        return t("schedule.backoffUntil", {
          when: cooldown.toLocaleString(),
        });
      }
    }
    if (!sub.last_synced_at) return t("schedule.willSyncNext");
    const last = new Date(sub.last_synced_at);
    const next = new Date(last.getTime() + sub.cooldown_minutes * 60_000);
    return t("schedule.nextAround", { when: next.toLocaleString() });
  }

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
    setFolderInput(initial.folder_path);
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
      setError(e instanceof Error ? e.message : t("errors.updateFailed"));
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
      setError(e instanceof Error ? e.message : t("errors.syncFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshMetadata() {
    setBusy(true);
    setError(null);
    try {
      await refreshSubscriptionMetadata(drive, subscription.id);
      setSubscription((s) => ({ ...s }));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.refreshFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleBackfill() {
    setBackfilling(true);
    setBackfillMessage(null);
    try {
      await extendBackfill(drive, subscription.id, backfillCount);
      setBackfillMessage(t("items.backfillQueued"));
      loadVideos();
    } catch {
      setBackfillMessage(t("items.backfillFailed"));
    } finally {
      setBackfilling(false);
    }
  }

  async function handleDelete() {
    const importedCount = videos.filter((v) => v.status === "imported").length;
    const message = t("danger.confirm", {
      title: subscription.display_title || subscription.source_ref,
      count: importedCount,
    });
    if (!confirm(message)) return;

    setBusy(true);
    setError(null);
    try {
      await deleteSubscription(drive, subscription.id);
      onChanged(null);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.deleteFailed"));
      setBusy(false);
    }
  }

  async function handleRetry(itemId: string) {
    setRetrying((prev) => new Set(prev).add(itemId));
    try {
      await retrySubscriptionVideo(drive, subscription.id, itemId);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.retryFailed"));
    } finally {
      setRetrying((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  async function handleDismiss(itemId: string) {
    setDismissing((prev) => new Set(prev).add(itemId));
    try {
      await dismissSubscriptionVideo(drive, subscription.id, itemId);
      setVideos((prev) =>
        prev.map((v) =>
          v.item_id === itemId ? { ...v, error_kind: "dismissed" } : v,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.dismissFailed"));
    } finally {
      setDismissing((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }

  const failed = videos.filter(
    (v) => v.status === "failed" && v.error_kind !== "dismissed",
  );
  const imported = videos.filter((v) => v.status === "imported");
  const dismissed = videos.filter(
    (v) => v.status === "failed" && v.error_kind === "dismissed",
  );

  const status = deriveStatus(subscription, failed.length);
  const importedCount = imported.length;
  const displayTitle =
    subscription.display_title || subscription.source_ref;
  const isPlaylist = subscription.source_kind === "playlist";

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={onClose}
      data-testid="detail-panel"
    >
      <aside
        className="flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-bg-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-bg-border px-5 py-4">
          <div className="flex items-start gap-3 min-w-0">
            <div
              className={`size-12 shrink-0 overflow-hidden bg-bg-elevated ${
                isPlaylist ? "rounded-xl" : "rounded-full"
              }`}
            >
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
                {displayTitle}
              </h2>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                <span className="capitalize">{subscription.provider}</span>
                <span>·</span>
                <span className="capitalize">{subscription.source_kind}</span>
              </div>
              <div className="mt-2">
                <SubscriptionStatusPill status={status} />
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
            aria-label={t("close")}
          >
            <X size={18} />
          </button>
        </header>

        <section className="border-b border-bg-border px-5 py-4">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => applyPatch({ is_enabled: !subscription.is_enabled })}
              disabled={busy}
              className="flex items-center gap-1 rounded-2xl bg-sand px-3 py-1.5 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
              data-testid="action-pause"
            >
              {subscription.is_enabled
                ? t("actions.pause")
                : t("actions.resume")}
            </button>
            <button
              type="button"
              onClick={handleSync}
              disabled={busy || subscription.running}
              className="flex items-center gap-1 rounded-2xl bg-sand px-3 py-1.5 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
              data-testid="action-sync"
            >
              <RefreshCw
                size={12}
                className={subscription.running ? "animate-spin" : ""}
              />
              {t("actions.syncNow")}
            </button>
            <button
              type="button"
              onClick={handleRefreshMetadata}
              disabled={busy}
              className="flex items-center gap-1 rounded-2xl bg-sand px-3 py-1.5 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
              data-testid="action-refresh-metadata"
            >
              {t("actions.refreshMetadata")}
            </button>
          </div>
          {error && (
            <div
              className="mt-3 rounded-2xl bg-danger/10 px-3 py-2 text-xs text-danger"
              data-testid="panel-error"
            >
              {error}
            </div>
          )}
        </section>

        <section className="border-b border-bg-border px-5 py-4">
          <h3 className="text-[11px] font-semibold uppercase text-text-muted">
            {t("schedule.heading")}
          </h3>
          <p className="mt-1.5 text-xs text-text-muted">
            {nextSyncHint(subscription)}
          </p>
          <div className="mt-3 flex items-center gap-2">
            <label className="text-xs text-text-muted">
              {t("schedule.cooldownLabel")}
            </label>
            <input
              type="number"
              min={1}
              value={editingCooldown}
              onChange={(e) =>
                setEditingCooldown(Math.max(1, Number(e.target.value) || 1))
              }
              className="w-20 rounded-2xl border border-bg-border bg-bg-primary px-3 py-1 text-xs text-text-primary focus:border-focus-ring focus:outline-none"
              data-testid="cooldown-input"
            />
            <button
              type="button"
              onClick={() =>
                applyPatch({ cooldown_minutes: editingCooldown })
              }
              disabled={busy || editingCooldown === subscription.cooldown_minutes}
              className="rounded-2xl bg-sand px-3 py-1 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
              data-testid="cooldown-save"
            >
              {t("schedule.cooldownSave")}
            </button>
          </div>
        </section>

        <section className="border-b border-bg-border px-5 py-4">
          <h3 className="text-[11px] font-semibold uppercase text-text-muted">
            {t("destination.heading")}
          </h3>
          {editingFolder ? (
            <div className="mt-2 flex items-center gap-2">
              <input
                type="text"
                value={folderInput}
                onChange={(e) => setFolderInput(e.target.value)}
                placeholder={t("destination.placeholder")}
                className="min-w-0 flex-1 rounded-2xl border border-bg-border bg-bg-primary px-3 py-1 text-xs text-text-primary focus:border-focus-ring focus:outline-none"
                data-testid="folder-input"
              />
              <button
                type="button"
                onClick={async () => {
                  await applyPatch({ folder_path: folderInput });
                  setEditingFolder(false);
                }}
                disabled={busy || folderInput === subscription.folder_path}
                className="rounded-2xl bg-sand px-3 py-1 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
                data-testid="folder-save"
              >
                {t("destination.save")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setFolderInput(subscription.folder_path);
                  setEditingFolder(false);
                }}
                className="rounded-2xl px-3 py-1 text-xs text-text-muted hover:bg-bg-elevated"
                data-testid="folder-cancel"
              >
                {t("destination.cancel")}
              </button>
            </div>
          ) : (
            <div className="mt-1.5 flex items-center gap-2">
              <span className="break-anywhere text-xs text-text-primary">
                {subscription.folder_path
                  ? `/${subscription.folder_path}`
                  : "/"}
              </span>
              <button
                type="button"
                onClick={() => setEditingFolder(true)}
                className="rounded p-0.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                aria-label={t("destination.edit")}
                data-testid="folder-edit"
              >
                <Pencil size={12} />
              </button>
            </div>
          )}
        </section>

        <section
          className="border-b border-bg-border px-5 py-4"
          data-testid="display-mode-section"
        >
          {/* Presentation only. Changing this never re-imports,
              reindexes, moves, or deletes anything (spec §3.2). */}
          <DisplayModeField
            name={`subscription-${subscription.id}-display-mode`}
            value={subscription.display_mode}
            onChange={(mode) => applyPatch({ display_mode: mode })}
            disabled={busy}
          />
        </section>

        <section className="border-b border-bg-border px-5 py-4">
          <h3 className="text-[11px] font-semibold uppercase text-text-muted">
            {t("options.heading")}
          </h3>
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
            {t("options.includeNoTranscript")}
          </label>
        </section>

        <section className="border-b border-bg-border px-5 py-4">
          <h3 className="mb-2 text-[11px] font-semibold uppercase text-text-muted">
            {t("items.heading")}
          </h3>
          {loadingVideos ? (
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <Loader2 size={12} className="animate-spin" />
              {t("items.loading")}
            </div>
          ) : videos.length === 0 ? (
            <div className="text-xs text-text-muted">{t("items.empty")}</div>
          ) : (
            <div className="-mx-5">
              {failed.length > 0 && (
                <div data-testid="items-failed-group">
                  <div className="px-5 pb-1 text-[11px] font-semibold uppercase text-accent-amber">
                    {t("items.groupAttention", { count: failed.length })}
                  </div>
                  <ul className="divide-y divide-bg-border">
                    {failed.map((v) => (
                      <SubscriptionItemRow
                        key={v.item_id}
                        video={v}
                        retrying={retrying.has(v.item_id)}
                        dismissing={dismissing.has(v.item_id)}
                        onRetry={() => handleRetry(v.item_id)}
                        onResolveConflict={() => setConflictItem(v.item_id)}
                        onDismiss={() => handleDismiss(v.item_id)}
                      />
                    ))}
                  </ul>
                </div>
              )}
              {imported.length > 0 && (
                <div data-testid="items-imported-group">
                  <div className="mt-3 px-5 pb-1 text-[11px] font-semibold uppercase text-text-muted">
                    {t("items.groupImported", { count: imported.length })}
                  </div>
                  <ul className="divide-y divide-bg-border">
                    {imported.slice(0, 50).map((v) => (
                      <SubscriptionItemRow
                        key={v.item_id}
                        video={v}
                        retrying={false}
                        dismissing={false}
                        onRetry={() => {}}
                        onResolveConflict={() => {}}
                        onDismiss={() => {}}
                      />
                    ))}
                  </ul>
                </div>
              )}
              {dismissed.length > 0 && (
                <div data-testid="items-dismissed-group">
                  <div className="mt-3 px-5 pb-1 text-[11px] font-semibold uppercase text-text-muted">
                    {t("items.groupSkipped", { count: dismissed.length })}
                  </div>
                </div>
              )}
            </div>
          )}
          <div className="mt-4 flex items-center gap-2" data-testid="backfill-row">
            <input
              type="number"
              min={1}
              max={200}
              value={backfillCount}
              onChange={(e) =>
                setBackfillCount(
                  Math.min(200, Math.max(1, Number(e.target.value) || 1)),
                )
              }
              className="w-20 rounded-2xl border border-bg-border bg-bg-primary px-3 py-1 text-xs text-text-primary focus:border-focus-ring focus:outline-none"
              data-testid="backfill-count"
            />
            <button
              type="button"
              onClick={handleBackfill}
              disabled={backfilling}
              className="rounded-2xl bg-sand px-3 py-1.5 text-xs text-text-primary hover:bg-sand-hover disabled:opacity-50"
              data-testid="backfill-button"
            >
              {backfilling ? t("items.backfilling") : t("items.backfill")}
            </button>
            {backfillMessage && (
              <span className="text-xs text-text-muted" data-testid="backfill-message">
                {backfillMessage}
              </span>
            )}
          </div>
        </section>

        <section className="px-5 py-4">
          <h3 className="text-[11px] font-semibold uppercase text-danger">
            {t("danger.heading")}
          </h3>
          <button
            type="button"
            onClick={handleDelete}
            disabled={busy}
            className="mt-2 flex items-center gap-1 rounded-2xl border border-danger/40 px-3 py-1.5 text-xs text-danger hover:bg-danger/10 disabled:opacity-50"
            data-testid="action-delete"
          >
            <Trash2 size={12} />
            {t("danger.delete")}
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
