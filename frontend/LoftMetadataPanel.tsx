"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getLoftMetadata, refreshLoft, type LoftMetadata } from "./api";
import CaptionStatusBadge from "./CaptionStatusBadge";

/**
 * LoftMetadataPanel — channel/description/captions-status panel rendered
 * below the Core LoftPlayer. Owned by the Media Import addon (Phase 1).
 */
export default function LoftMetadataPanel({ fileId }: { fileId: string }) {
  const [metadata, setMetadata] = useState<LoftMetadata | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    getLoftMetadata(fileId).then(setMetadata);
  }, [fileId]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refreshLoft(fileId);
      // TODO: replace polling with a WS event listener for the loft fetch.
      setTimeout(() => {
        getLoftMetadata(fileId).then(setMetadata);
        setRefreshing(false);
      }, 3000);
    } catch {
      setRefreshing(false);
    }
  }

  if (!metadata) return null;

  return (
    <>
      <div className="mt-3 flex items-start gap-2">
        <div className="min-w-0 flex-1 text-xs text-text-muted">
          {metadata.channel && (
            <span className="font-medium text-text-primary">
              {metadata.channel}
            </span>
          )}
          {metadata.published_at && <span> · {metadata.published_at}</span>}
          {metadata.description && (
            <p className="mt-1 line-clamp-3 whitespace-pre-wrap">
              {metadata.description}
            </p>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="shrink-0 rounded-lg p-1.5 text-text-muted hover:bg-bg-card hover:text-text-primary disabled:opacity-50"
          title="Refresh metadata"
        >
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>
      <CaptionStatusBadge
        metadata={metadata}
        onRetry={handleRefresh}
        isRetrying={refreshing}
      />
    </>
  );
}
