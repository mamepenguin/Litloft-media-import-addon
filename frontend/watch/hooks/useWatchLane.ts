"use client";

import { useCallback, useEffect, useState } from "react";

import { listWatch, WATCH_PAGE_SIZE, type WatchItem, type WatchLane } from "../../api";

export interface WatchLaneState {
  items: WatchItem[];
  loading: boolean;
  /** True while a "load more" page is in flight, not the first load. */
  loadingMore: boolean;
  error: string | null;
  /** A full page came back, so another one may exist. */
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
 */
export function useWatchLane(drive: string, lane: WatchLane): WatchLaneState {
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
    listWatch(drive, lane)
      .then((rows) => {
        if (cancelled) return;
        setItems(rows);
        setHasMore(rows.length === WATCH_PAGE_SIZE);
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
  }, [drive, lane, reloadKey]);

  const loadMore = useCallback(() => {
    if (loadingMore || loading || !hasMore) return;
    setLoadingMore(true);
    listWatch(drive, lane, { offset: items.length })
      .then((rows) => {
        // Offset paging can repeat an item if a sync lands between
        // pages. Dropping duplicates here keeps React keys unique
        // rather than trading a cursor API for a rare edge case.
        setItems((prev) => {
          const seen = new Set(prev.map((i) => i.file_id));
          return [...prev, ...rows.filter((r) => !seen.has(r.file_id))];
        });
        setHasMore(rows.length === WATCH_PAGE_SIZE);
      })
      .catch(() => {
        // A failed "load more" leaves what is already on screen alone;
        // the button stays available for another try.
        setHasMore(false);
      })
      .finally(() => setLoadingMore(false));
  }, [drive, lane, items.length, hasMore, loading, loadingMore]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return { items, loading, loadingMore, error, hasMore, loadMore, reload };
}
