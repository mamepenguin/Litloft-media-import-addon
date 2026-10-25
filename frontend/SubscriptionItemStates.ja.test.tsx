/**
 * Japanese copy for the new item groups and the rate-limit notice
 * (SPEC-ADDON-027, SPEC-ADDON-028, SPEC-ADDON-029). The shared test setup
 * renders English, so this file resolves keys against the merged ja tree.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import SubscriptionDetailPanel from "@/addons/media_import/SubscriptionDetailPanel";
import type { Subscription, SubscriptionVideo } from "@/addons/media_import/api";

const mockListVideos = vi.fn();
const ws = vi.hoisted(() => ({
  event: null as { event: string; data: Record<string, unknown> } | null,
}));

// One translator per namespace, as next-intl returns: the shared setup builds
// a new function per call, and an effect keyed on `t` then re-runs forever.
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
  const cache = new Map<string, (key: string, values?: Record<string, unknown>) => string>();
  const useTranslations = (namespace?: string) => {
    const ns = namespace ?? "";
    if (!cache.has(ns)) {
      cache.set(ns, (key, values) => {
        const path = namespace === undefined ? key : `${namespace}.${key}`;
        const raw = lookup(path);
        let text = typeof raw === "string" ? raw : path;
        for (const [k, v] of Object.entries(values ?? {})) {
          text = text.replace(`{${k}}`, String(v));
        }
        return text;
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

// A stable router object, as next/navigation's is: a fresh one per render
// re-fires every effect that depends on it.
const router = vi.hoisted(() => ({ push: () => {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    listSubscriptionVideos: (...args: unknown[]) => mockListVideos(...args),
    patchSubscription: vi.fn(),
    extendBackfill: vi.fn(),
    deleteSubscription: vi.fn(),
    syncSubscription: vi.fn(),
    refreshSubscriptionMetadata: vi.fn(),
    subscriptionAvatarUrl: (id: number) => `/avatar/${id}`,
  };
});

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: (filter?: string) =>
    ws.event && (!filter || ws.event.event === filter) ? ws.event : null,
}));

const subscription: Subscription = {
  id: 1,
  provider: "youtube",
  source_kind: "channel",
  source_ref: "UCabc",
  drive: "d",
  folder_path: "",
  title: null,
  is_enabled: true,
  cooldown_minutes: 60,
  include_no_transcript: false,
  last_synced_at: null,
  cooldown_until: null,
  created_at: "2026-05-01T00:00:00",
  running: false,
  avatar_url: null,
  display_title: "Test Channel",
  display_mode: "library",
};

function video(item_id: string, error_kind: string, title: string | null): SubscriptionVideo {
  return {
    subscription_id: 1,
    item_id,
    status: "failed",
    error_kind,
    file_id: null,
    first_seen_at: "2026-05-01T00:00:00",
    last_attempted_at: null,
    title,
    thumbnail_path: null,
    channel: null,
    published_at: null,
    source_url: `https://www.youtube.com/watch?v=${item_id}`,
  } as SubscriptionVideo;
}

const props = { drive: "d", subscription, onClose: vi.fn(), onChanged: vi.fn() };

function hasHint(text: string): boolean {
  return (
    screen.queryAllByText(text).length > 0 ||
    document.querySelector(`[title="${text}"]`) !== null ||
    document.querySelector(`[aria-description="${text}"]`) !== null
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  ws.event = null;
  mockListVideos.mockResolvedValue([
    video("pen00000001", "retry_pending", "Pending one"),
    video("upc00000001", "upcoming", null),
    video("prm00000001", "permanent", "Members only one"),
  ]);
});

describe("SPEC-ADDON-027/028/029 Japanese copy", () => {
  it("renders the new groups, labels and hints in Japanese", async () => {
    render(<SubscriptionDetailPanel {...props} />);

    expect(await screen.findByText("再試行待ち (2)")).toBeInTheDocument();
    expect(screen.getByText("取得不可 (1)")).toBeInTheDocument();
    expect(screen.getAllByText("再試行待ち", { exact: true })).toHaveLength(1);
    expect(screen.getAllByText("配信前", { exact: true })).toHaveLength(1);
    expect(screen.getAllByText("取得不可", { exact: true })).toHaveLength(1);
    expect(hasHint("次回の同期で自動的に再試行します。")).toBe(true);
    expect(hasHint("配信・プレミア公開が始まったら取り込みます。")).toBe(true);
    expect(hasHint("メンバー限定・年齢制限・非公開・削除などで取得できません。")).toBe(true);
    expect(hasHint("非公開・削除・地域制限の可能性があります。")).toBe(false);
    expect(screen.getByText("未取得の動画")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "元のページを開く" })).toHaveLength(3);
  });

  it("renders the rate-limit notice in Japanese", async () => {
    const { rerender } = render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("再試行待ち (2)");
    ws.event = {
      event: "media_import.subscription.sync_completed",
      data: { subscription_id: 1, drive: "d", rate_limited: true },
    };
    rerender(<SubscriptionDetailPanel {...props} />);

    expect(
      await screen.findByText(
        "YouTube に一時的に制限されています。しばらくしてから再試行してください。",
      ),
    ).toBeInTheDocument();
  });
});
