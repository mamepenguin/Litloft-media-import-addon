"use client";

import { useTranslations } from "next-intl";

import type { WatchItem } from "../api";
import type { WatchLaneState } from "./hooks/useWatchLane";
import WatchLaneSection from "./WatchLaneSection";

export interface WatchViewPresenterProps {
  continueWatching: WatchLaneState;
  regular: WatchLaneState;
  feed: WatchLaneState;
  /** False when no subscription on this drive is set to feed/regular. */
  hasSurfacedSources: boolean;
  onAddToCollection: (item: WatchItem) => void;
  onGoToManage: () => void;
}

export default function WatchViewPresenter({
  continueWatching,
  regular,
  feed,
  hasSurfacedSources,
  onAddToCollection,
  onGoToManage,
}: WatchViewPresenterProps) {
  const t = useTranslations("mediaImport.watch");

  const anyLoading =
    continueWatching.loading || regular.loading || feed.loading;
  const nothingToShow =
    !anyLoading &&
    continueWatching.items.length === 0 &&
    regular.items.length === 0 &&
    feed.items.length === 0;

  return (
    <div className="space-y-8" data-testid="watch-view">
      <WatchLaneSection
        testId="watch-lane-continue"
        heading={t("lane.continue")}
        state={continueWatching}
        onAddToCollection={onAddToCollection}
      />
      <WatchLaneSection
        testId="watch-lane-regular"
        heading={t("lane.regular")}
        state={regular}
        onAddToCollection={onAddToCollection}
      />
      <WatchLaneSection
        testId="watch-lane-feed"
        heading={t("lane.feed")}
        state={feed}
        onAddToCollection={onAddToCollection}
      />

      {nothingToShow && (
        <div
          className="rounded-xl border border-dashed border-bg-border px-4 py-10 text-center"
          data-testid="watch-empty"
        >
          {/* Nothing here means nothing has been *surfaced* — never that
              importing is unfinished. Every imported video is already
              searchable whether or not it ever appears in Watch. */}
          <p className="text-sm text-text-muted">
            {hasSurfacedSources ? t("empty.surfaced") : t("empty.none")}
          </p>
          <button
            type="button"
            onClick={onGoToManage}
            className="mt-3 rounded-2xl bg-sand px-4 py-2 text-sm text-text-primary transition-colors hover:bg-sand-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
          >
            {t("empty.action")}
          </button>
        </div>
      )}
    </div>
  );
}
