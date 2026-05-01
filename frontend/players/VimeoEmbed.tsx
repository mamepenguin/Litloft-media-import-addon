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

export default function VimeoEmbed({ url }: LoftEmbedProps) {
  const videoId = useMemo(() => extractVimeoId(url), [url]);

  if (!videoId) return null;

  const src = `https://player.vimeo.com/video/${videoId}`;

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
