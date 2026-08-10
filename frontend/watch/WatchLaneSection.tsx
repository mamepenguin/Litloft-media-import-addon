"use client";

import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

import type { WatchItem } from "../api";
import type { WatchLaneState } from "./hooks/useWatchLane";
import WatchCard from "./WatchCard";

interface Props {
  heading: string;
  state: WatchLaneState;
  onAddToCollection: (item: WatchItem) => void;
  testId: string;
}

function SkeletonCard() {
  return (
    <div className="animate-pulse overflow-hidden rounded-xl bg-bg-card">
      <div className="aspect-video bg-bg-elevated" />
      <div className="space-y-2 p-3">
        <div className="h-4 w-3/4 rounded-lg bg-bg-elevated" />
        <div className="h-3 w-1/2 rounded-lg bg-bg-elevated" />
      </div>
    </div>
  );
}

/**
 * One lane of the Watch surface.
 *
 * An empty lane renders nothing at all — no zero-count, no "you're all
 * caught up". A lane that failed to load says so in place and leaves
 * the other two alone (spec §7).
 */
export default function WatchLaneSection({
  heading,
  state,
  onAddToCollection,
  testId,
}: Props) {
  const t = useTranslations("mediaImport.watch");

  if (state.error) {
    return (
      <section data-testid={`${testId}-error`}>
        <h2 className="mb-3 text-lg font-bold text-text-primary">{heading}</h2>
        <div className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
          {state.error}
        </div>
      </section>
    );
  }

  if (state.loading) {
    return (
      <section data-testid={`${testId}-loading`}>
        <h2 className="mb-3 text-lg font-bold text-text-primary">{heading}</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </section>
    );
  }

  if (state.items.length === 0) return null;

  return (
    <section data-testid={testId}>
      <h2 className="mb-3 text-lg font-bold text-text-primary">{heading}</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {state.items.map((item) => (
          <WatchCard
            key={item.file_id}
            item={item}
            onAddToCollection={onAddToCollection}
          />
        ))}
      </div>
      {state.hasMore && (
        <div className="mt-3 flex justify-center">
          <button
            type="button"
            onClick={state.loadMore}
            disabled={state.loadingMore}
            className="inline-flex items-center gap-1.5 rounded-2xl bg-sand px-4 py-2 text-sm text-text-primary transition-colors hover:bg-sand-hover disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
          >
            {state.loadingMore && (
              <Loader2 size={14} className="animate-spin" />
            )}
            {t("loadMore")}
          </button>
        </div>
      )}
    </section>
  );
}
