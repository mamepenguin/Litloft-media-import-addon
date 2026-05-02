"use client";

import { useMemo } from "react";
import type { LoftEmbedProps } from "@/components/loft/types";

const VIMEO_HOSTS = new Set([
  "vimeo.com",
  "www.vimeo.com",
  "player.vimeo.com",
]);

export function extractVimeoId(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!VIMEO_HOSTS.has(parsed.hostname)) return null;
  const patterns = [
    /^\/video\/(\d+)/,
    /^\/(?:channels\/[\w-]+\/|groups\/[\w-]+\/videos\/)?(\d+)/,
  ];
  for (const re of patterns) {
    const m = parsed.pathname.match(re);
    if (m?.[1]) return m[1];
  }
  return null;
}

export default function VimeoEmbed({ url, initialTime }: LoftEmbedProps) {
  const videoId = useMemo(() => extractVimeoId(url), [url]);

  if (!videoId) return null;

  // Vimeo's player honours `#t=<seconds>s` in the iframe URL on initial
  // load. We don't currently embed @vimeo/player for runtime seeks,
  // so a same-file ?t= change requires a remount — which is what the
  // file detail page already does when its searchParams change.
  const seekFrag =
    Number.isFinite(initialTime) && (initialTime ?? 0) > 0
      ? `#t=${Math.floor(initialTime as number)}s`
      : "";
  const src = `https://player.vimeo.com/video/${videoId}${seekFrag}`;

  return (
    <div
      className="relative w-full overflow-hidden bg-black md:rounded-xl"
      style={{ paddingTop: "56.25%" }}
    >
      <iframe
        src={src}
        title="Vimeo player"
        className="absolute inset-0 h-full w-full border-0"
        allow="autoplay; fullscreen; picture-in-picture"
        allowFullScreen
      />
    </div>
  );
}
