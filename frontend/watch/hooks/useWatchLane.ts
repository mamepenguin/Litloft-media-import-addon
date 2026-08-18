"use client";

import { useCallback, useEffect, useState } from "react";

import {
  listWatch,
  WATCH_LANE_CONFIG,
  type WatchItem,
  type WatchLane,
  type WatchLaneConfig,
} from "../../api";

/**
 * "A full page came back" is the only has-more signal there is, and a
 * bounded lane must refuse it — being full is what a lane capped at 6
 * or 12 always looks like.
 *
 * One function rather than the same expression at both call sites: the
 * first page and a later one have to agree, and two independent copies
 * of one rule drift.
 */
function hasAnotherPage(
  rows: WatchItem[],
  { pageable, limit }: WatchLaneConfig,
): boolean {
  return pageable && rows.length === limit;
}

export interface WatchLaneState {
  items: WatchItem[];
  loading: boolean;
  /** True while a "load more" page is in flight, not the first load. */
  loadingMore: boolean;
  error: string | null;
  /**
   * A full page came back on a lane that pages, so another one may
   * exist. Always false on a bounded lane — see `WATCH_LANE_CONFIG`.
   */
  hasMore: boolean;
  loadMore: () => void;
  reload: () => void;
}

/**
 * Owns one Watch lane's items and paging.
 *
 * Each lane is fetched and paged on its own — a single shared limit
 * would be wrong for slices that grow at completely different rates.
 * There is no total to report: "a full page came back" is the only
 * has-more signal, deliberately, because Watch must never render a
 * backlog count (spec §2.2).
 *
 * On a lane declared non-pageable that signal is refused outright, so
 * a bounded lane cannot infer more pages from its own fullness — which
 * is exactly what a lane capped at 6 or 12 always looks like.
 */
export function useWatchLane(drive: string, lane: WatchLane): WatchLaneState {
  const config = WATCH_LANE_CONFIG[lane];
  const { limit } = config;
  const [items, setItems] = useState<WatchItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!drive) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    listWatch(drive, lane, { limit })
      .then((rows) => {
        if (cancelled) return;
        setItems(rows);
        setHasMore(hasAnotherPage(rows, config));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setItems([]);
        setHasMore(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [drive, lane, limit, config, reloadKey]);

  const loadMore = useCallback(() => {
    if (loadingMore || loading || !hasMore) return;
    setLoadingMore(true);
    listWatch(drive, lane, { limit, offset: items.length })
      .then((rows) => {
        // Offset paging can repeat an item if a sync lands between
        // pages. Dropping duplicates here keeps React keys unique
        // rather than trading a cursor API for a rare edge case.
        setItems((prev) => {
          const seen = new Set(prev.map((i) => i.file_id));
          return [...prev, ...rows.filter((r) => !seen.has(r.file_id))];
        });
        setHasMore(hasAnotherPage(rows, config));
      })
      .catch(() => {
        // A failed "load more" leaves what is already on screen alone;
        // the button stays available for another try.
        setHasMore(false);
      })
      .finally(() => setLoadingMore(false));
  }, [drive, lane, limit, config, items.length, hasMore, loading, loadingMore]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return { items, loading, loadingMore, error, hasMore, loadMore, reload };
}
