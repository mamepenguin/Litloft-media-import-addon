"use client";

/**
 * The refresh entry lives in the `⋮` menu and the panel under the player;
 * two `AddonSlot`s with no shared ancestor, so the panel hears about a
 * refresh through a module-level channel.
 */

import { useEffect, useRef } from "react";

const listeners = new Set<(fileId: string) => void>();

export function notifyLoftRefreshed(fileId: string): void {
  for (const listener of listeners) listener(fileId);
}

export function useLoftRefreshed(fileId: string, handler: () => void): void {
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });

  useEffect(() => {
    const listener = (refreshed: string) => {
      if (refreshed === fileId) handlerRef.current();
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [fileId]);
}
