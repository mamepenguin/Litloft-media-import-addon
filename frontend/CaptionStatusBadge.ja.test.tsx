/**
 * SPEC-ADDON-190 I5 in Japanese. The shared test setup renders English, so
 * this file resolves keys against the merged ja tree.
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import CaptionStatusBadge from "@/addons/media_import/CaptionStatusBadge";
import type { LoftMetadata } from "@/addons/media_import/api";

vi.mock("next-intl", async () => {
  const ja = (await import("@/messages/ja.json")).default as Record<string, unknown>;
  const lookup = (path: string): unknown =>
    path.split(".").reduce<unknown>(
      (node, part) =>
        typeof node === "object" && node !== null
          ? (node as Record<string, unknown>)[part]
          : undefined,
      ja,
    );
  const cache = new Map<string, (key: string) => string>();
  const useTranslations = (namespace?: string) => {
    const ns = namespace ?? "";
    if (!cache.has(ns)) {
      cache.set(ns, (key) => {
        const raw = lookup(namespace === undefined ? key : `${namespace}.${key}`);
        return typeof raw === "string" ? raw : key;
      });
    }
    return cache.get(ns)!;
  };
  return {
    useTranslations,
    useLocale: () => "ja",
    NextIntlClientProvider: ({ children }: { children: React.ReactNode }) => children,
  };
});

function noCaptions(provider: string, url: string): LoftMetadata {
  return {
    provider,
    url,
    description: null,
    channel: null,
    published_at: null,
    language: null,
    has_captions: false,
    captions_downloaded: false,
    caption_error_kind: null,
    fetched_at: "2026-04-26T00:00:00Z",
    fetch_error: null,
  };
}

describe("SPEC-ADDON-190 the caption badge in Japanese", () => {
  it.each([
    ["youtube", "https://www.youtube.com/watch?v=abc"],
    ["vimeo", "https://vimeo.com/123456"],
  ])("SPEC-ADDON-190 I5: the no-captions label for a %s video names no provider", (provider, url) => {
    const { container, getByText } = render(
      <CaptionStatusBadge metadata={noCaptions(provider, url)} />,
    );
    expect(getByText("この動画には字幕がありません")).toBeTruthy();
    expect(container.textContent).not.toMatch(/youtube|vimeo/i);
  });
});
