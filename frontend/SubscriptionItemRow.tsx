"use client";

import { useRouter } from "next/navigation";
import { ExternalLink, RefreshCw } from "lucide-react";

import type { SubscriptionVideo } from "./api";
import { describeError, isRetryable } from "./lib/errorMessages";

interface Props {
  video: SubscriptionVideo;
  retrying: boolean;
  onRetry: () => void;
  onResolveConflict: () => void;
}

/**
 * One row in the items list inside the subscription detail panel.
 *
 * Surfaces:
 *   - Status (imported / failed / pending), color-coded
 *   - error_kind translated to user-language via errorMessages dictionary
 *   - Retry button only when isRetryable() (so dismissed / permanent
 *     suppress it)
 *   - Resolve-conflict link for path_conflict specifically
 *   - Click-through to the file detail page when imported
 */

export default function SubscriptionItemRow({
  video,
  retrying,
  onRetry,
  onResolveConflict,
}: Props) {
  const router = useRouter();
  const error = describeError(video.error_kind);
  const showRetry =
    video.status === "failed" && isRetryable(video.error_kind);
  const showResolveConflict =
    video.status === "failed" && video.error_kind === "path_conflict";

  const statusBg =
    video.status === "imported"
      ? "bg-success/10 text-success"
      : video.status === "failed"
        ? "bg-danger/10 text-danger"
        : "bg-warning/10 text-warning";

  return (
    <li
      className="flex items-start gap-3 px-4 py-2 text-sm"
      data-testid={`item-row-${video.item_id}`}
    >
      <span
        className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${statusBg}`}
      >
        {video.status === "imported"
          ? "Imported"
          : video.status === "failed"
            ? error?.label ?? "Failed"
            : "Pending"}
      </span>
      <div className="flex-1 min-w-0">
        <div className="truncate text-text-primary">{video.item_id}</div>
        {error && video.status === "failed" && (
          <p className="mt-0.5 text-xs text-text-muted">{error.hint}</p>
        )}
      </div>
      {video.status === "imported" && video.file_id && (
        <button
          type="button"
          onClick={() => router.push(`/files/${video.file_id}`)}
          className="shrink-0 rounded p-1 text-text-muted hover:text-text-primary"
          aria-label="Open file"
        >
          <ExternalLink size={14} />
        </button>
      )}
      {showResolveConflict && (
        <button
          type="button"
          onClick={onResolveConflict}
          className="shrink-0 rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover"
          data-testid={`resolve-conflict-${video.item_id}`}
        >
          Resolve…
        </button>
      )}
      {showRetry && !showResolveConflict && (
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="shrink-0 flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary hover:bg-bg-hover disabled:opacity-50"
          data-testid={`retry-${video.item_id}`}
        >
          <RefreshCw size={12} className={retrying ? "animate-spin" : ""} />
          Retry
        </button>
      )}
    </li>
  );
}
