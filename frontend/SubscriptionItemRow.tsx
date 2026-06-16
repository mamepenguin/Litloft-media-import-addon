"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { ExternalLink, RefreshCw, X } from "lucide-react";

import type { SubscriptionVideo } from "./api";
import { isRetryable, normalizeErrorKind } from "./lib/errorMessages";

interface Props {
  video: SubscriptionVideo;
  retrying: boolean;
  dismissing: boolean;
  onRetry: () => void;
  onResolveConflict: () => void;
  onDismiss: () => void;
}

/**
 * One row in the items list inside the subscription detail panel.
 *
 * Shows the imported video's title (sourced from loft_metadata via the
 * server-side JOIN) instead of the raw provider item id. Falls back to
 * a localized "untitled video" placeholder for items that never produced
 * a .loft (e.g. permanent failures before allocation). The provider id
 * itself stays out of the visible UI; if a developer needs it it lives
 * on the row's data-testid for inspection.
 */

export default function SubscriptionItemRow({
  video,
  retrying,
  dismissing,
  onRetry,
  onResolveConflict,
  onDismiss,
}: Props) {
  const router = useRouter();
  const tItem = useTranslations("mediaImport.item");
  const tError = useTranslations("mediaImport.errorKind");

  const errorKind = normalizeErrorKind(video.error_kind);
  const showRetry =
    video.status === "failed" && isRetryable(video.error_kind);
  const showResolveConflict =
    video.status === "failed" && video.error_kind === "path_conflict";
  const showDismiss =
    video.status === "failed" && video.error_kind !== "dismissed";

  const statusBg =
    video.status === "imported"
      ? "bg-accent-teal/15 text-accent-teal"
      : video.status === "failed"
        ? "bg-danger/10 text-danger"
        : "bg-accent-amber/15 text-accent-amber";

  const statusLabel =
    video.status === "imported"
      ? tItem("statusImported")
      : video.status === "failed"
        ? errorKind
          ? tError(`${errorKind}.label`)
          : tItem("statusFailed")
        : tItem("statusPending");

  const title = video.title ?? tItem("untitled");

  return (
    <li
      className="flex items-start gap-3 px-5 py-2.5 text-sm"
      data-testid={`item-row-${video.item_id}`}
    >
      <span
        className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${statusBg}`}
      >
        {statusLabel}
      </span>
      <div className="flex-1 min-w-0">
        <div className="truncate text-text-primary">{title}</div>
        {video.channel && (
          <div className="mt-0.5 truncate text-xs text-text-muted">
            {video.channel}
          </div>
        )}
        {errorKind && video.status === "failed" && (
          <p className="mt-1 text-xs text-text-muted">
            {tError(`${errorKind}.hint`)}
          </p>
        )}
      </div>
      {video.status === "imported" && video.file_id && (
        <button
          type="button"
          onClick={() => router.push(`/files/${video.file_id}`)}
          className="shrink-0 rounded-full p-1.5 text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          aria-label={tItem("openFile")}
        >
          <ExternalLink size={14} />
        </button>
      )}
      {showResolveConflict && (
        <button
          type="button"
          onClick={onResolveConflict}
          className="shrink-0 rounded-full px-2.5 py-1 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-primary"
          data-testid={`resolve-conflict-${video.item_id}`}
        >
          {tItem("resolveConflict")}
        </button>
      )}
      {showRetry && !showResolveConflict && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="shrink-0 flex items-center gap-1 rounded-full px-2.5 py-1 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
          data-testid={`retry-${video.item_id}`}
        >
          <RefreshCw size={12} className={retrying ? "animate-spin" : ""} />
          {tItem("retry")}
        </button>
      )}
      {showDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          disabled={dismissing}
          className="shrink-0 flex items-center gap-1 rounded-full px-2.5 py-1 text-xs text-text-muted hover:bg-bg-elevated hover:text-text-primary disabled:opacity-50"
          data-testid={`dismiss-${video.item_id}`}
        >
          <X size={12} />
          {tItem("dismiss")}
        </button>
      )}
    </li>
  );
}
