"use client";

import { Loader2 } from "lucide-react";

import {
  subscriptionAvatarUrl,
  type Subscription,
} from "./api";
import SubscriptionStatusPill, {
  deriveStatus,
} from "./SubscriptionStatusPill";

interface Props {
  subscription: Subscription;
  /** Imported count for the badge under the title. */
  importedCount: number;
  /** Failed (non-dismissed) count, used for the status pill. */
  failedCount: number;
  onClick: () => void;
}

/**
 * One subscription card on the dashboard grid.
 *
 * Status-first layout: the pill is rendered prominently right next
 * to the avatar so users notice "Needs attention" before reading
 * any text. Avatar falls back to a gradient placeholder via the
 * onError handler when the cached jpeg is missing (Phase 2 installs
 * with no display_title / avatar yet).
 */

function nextSyncSummary(sub: Subscription): string {
  if (!sub.is_enabled) return "Paused";
  if (sub.cooldown_until && new Date(sub.cooldown_until) > new Date()) {
    return "Backoff active";
  }
  if (!sub.last_synced_at) return "Pending first sync";
  const last = new Date(sub.last_synced_at);
  const next = new Date(last.getTime() + sub.cooldown_minutes * 60_000);
  const ms = next.getTime() - Date.now();
  if (ms < 0) return "Sync due";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `Next: ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Next: ${hours}h`;
  return `Next: ${Math.round(hours / 24)}d`;
}

export default function SubscriptionCard({
  subscription,
  importedCount,
  failedCount,
  onClick,
}: Props) {
  const status = deriveStatus(subscription, failedCount);
  const isPlaylist = subscription.source_kind === "playlist";

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border border-border-primary bg-bg-card p-3 text-left hover:bg-bg-hover focus:border-accent-cta focus:outline-none"
      data-testid={`subscription-card-${subscription.id}`}
    >
      <div
        className={`size-12 shrink-0 overflow-hidden bg-bg-hover ${
          isPlaylist ? "rounded-lg" : "rounded-full"
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

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <SubscriptionStatusPill status={status} />
          {subscription.running && status !== "syncing" && (
            <Loader2 size={12} className="animate-spin text-accent-cta" />
          )}
        </div>
        <div className="mt-1 truncate text-sm font-medium text-text-primary">
          {subscription.display_title || subscription.source_ref}
        </div>
        <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-text-muted">
          <span className="capitalize">{subscription.provider}</span>
          <span>·</span>
          <span className="capitalize">{subscription.source_kind}</span>
          <span>·</span>
          <span>{importedCount} imported</span>
          {failedCount > 0 && (
            <>
              <span>·</span>
              <span className="text-warning">{failedCount} failed</span>
            </>
          )}
        </div>
        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-text-muted">
          <span className="truncate">
            {subscription.folder_path
              ? `/${subscription.folder_path}`
              : "drive root"}
          </span>
          <span className="shrink-0">{nextSyncSummary(subscription)}</span>
        </div>
      </div>
    </button>
  );
}
