"use client";

import { useState } from "react";

import { CollectionPicker } from "@/components/CollectionPicker";

import type { WatchItem } from "../api";
import { useWatchLane } from "./hooks/useWatchLane";
import WatchViewPresenter from "./WatchViewPresenter";

interface Props {
  drive: string;
  /**
   * Whether any subscription on this drive is non-`library`. Passed in
   * rather than fetched here: the page already loads the subscription
   * list to decide which view to land on, and asking twice for the same
   * answer buys nothing.
   */
  hasSurfacedSources: boolean;
  onGoToManage: () => void;
}

/**
 * The Watch surface: a read projection over the library.
 *
 * Three independently paged lanes plus core's own CollectionPicker for
 * the "actually watch this later" case. Media Import deliberately adds
 * no Watch Later storage of its own — an explicit intention to watch a
 * single video belongs in an existing Core Collection (spec §2.3).
 */
export default function WatchViewContainer({
  drive,
  hasSurfacedSources,
  onGoToManage,
}: Props) {
  const continueWatching = useWatchLane(drive, "continue");
  const regular = useWatchLane(drive, "regular");
  const feed = useWatchLane(drive, "feed");

  const [pickerTarget, setPickerTarget] = useState<WatchItem | null>(null);

  return (
    <>
      <WatchViewPresenter
        continueWatching={continueWatching}
        regular={regular}
        feed={feed}
        hasSurfacedSources={hasSurfacedSources}
        onAddToCollection={setPickerTarget}
        onGoToManage={onGoToManage}
      />
      <CollectionPicker
        open={pickerTarget !== null}
        drive={drive}
        fileIds={pickerTarget ? [pickerTarget.file_id] : []}
        onClose={() => setPickerTarget(null)}
      />
    </>
  );
}
