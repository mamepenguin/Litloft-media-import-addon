"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "media-import-youtube-ui";

export function readYouTubeUiPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage?.getItem?.(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/**
 * Whether to hand playback back to YouTube's own player UI instead of
 * drawing Litloft's.
 *
 * Off by default: Litloft's controls are the point of the embed, and
 * this trades away the gestures, the subtitle toggle and the speed
 * sheet along with them.
 */
export function useYouTubeUiPreference(): [boolean, (value: boolean) => void] {
  const [enabled, setEnabled] = useState(false);

  // Hydrated in an effect, not in the initial state: reading
  // localStorage during render would make the server and client
  // markup disagree.
  useEffect(() => {
    setEnabled(readYouTubeUiPreference());
  }, []);

  const update = useCallback((value: boolean) => {
    setEnabled(value);
    try {
      window.localStorage?.setItem?.(STORAGE_KEY, String(value));
    } catch {
      // localStorage unavailable (private mode, test env) — keep in-memory only
    }
  }, []);

  return [enabled, update];
}
