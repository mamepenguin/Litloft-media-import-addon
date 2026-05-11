"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  subscriptionAvatarUrl,
  type Subscription,
} from "./api";
import SubscriptionStatusPill, {
  deriveStatus,
} from "./SubscriptionStatusPill";

interface Props {
  subscription: Subscription;
  importedCount: number;
  failedCount: number;
  onClick: () => void;
}

/**
 * One subscription card on the dashboard grid.
 *
 * Status-first layout: the pill is rendered prominently right next
 * to the avatar so users notice "Needs attention" before reading
 * any text. Avatar shape signals provider intent — channel = round
 * (matches the YouTube channel avatar convention), playlist = rounded-lg
 * square (matches a stack of items).
 */

type SyncTranslator = ReturnType<typeof useTranslations<"mediaImport.card.nextSync">>;

function nextSyncSummary(sub: Subscription, t: SyncTranslator): string {
  if (!sub.is_enabled) return t("paused");
  if (sub.cooldown_until && new Date(sub.cooldown_until) > new Date()) {
    return t("backoff");
  }
  if (!sub.last_synced_at) return t("pendingFirst");
  const last = new Date(sub.last_synced_at);
  const next = new Date(last.getTime() + sub.cooldown_minutes * 60_000);
  const ms = next.getTime() - Date.now();
  if (ms < 0) return t("due");
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return t("minutes", { n: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 24) return t("hours", { n: hours });
  return t("days", { n: Math.round(hours / 24) });
}

export default function SubscriptionCard({
  subscription,
  importedCount,
  failedCount,
  onClick,
}: Props) {
  const status = deriveStatus(subscription, failedCount);
  const isPlaylist = subscription.source_kind === "playlist";
  const tCard = useTranslations("mediaImport.card");
  const tSync = useTranslations("mediaImport.card.nextSync");
  const displayTitle =
    subscription.display_title || subscription.source_ref;

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full flex-col gap-3 rounded-xl border border-bg-border bg-bg-card p-4 text-left transition-colors hover:bg-bg-elevated focus:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
      data-testid={`subscription-card-${subscription.id}`}
    >
      <div className="flex items-start gap-3">
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

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <SubscriptionStatusPill status={status} />
            {subscription.running && status !== "syncing" && (
              <Loader2 size={12} className="animate-spin text-accent" />
            )}
          </div>
          <div className="mt-1.5 truncate text-sm font-semibold text-text-primary">
            {displayTitle}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 truncate text-xs text-text-muted">
            <span className="capitalize">{subscription.provider}</span>
            <span>·</span>
            <span className="capitalize">{subscription.source_kind}</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 text-xs text-text-muted">
        <div className="flex items-center gap-2 truncate">
          <span>{tCard("imported", { count: importedCount })}</span>
          {failedCount > 0 && (
            <>
              <span>·</span>
              <span className="text-accent-amber">
                {tCard("failed", { count: failedCount })}
              </span>
            </>
          )}
        </div>
        <span className="shrink-0">{nextSyncSummary(subscription, tSync)}</span>
      </div>
    </button>
  );
}
