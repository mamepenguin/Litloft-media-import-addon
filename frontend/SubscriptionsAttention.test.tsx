/**
 * Needs attention on the dashboard: card failed count, status pill and the
 * attention filter count only the 要対応 group (SPEC-ADDON-027,
 * SPEC-ADDON-028, invariant I6).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import SubscriptionsDashboard from "@/addons/media_import/SubscriptionsDashboard";
import type {
  Subscription,
  SubscriptionSummary,
  SubscriptionVideo,
} from "@/addons/media_import/api";

const mockListSubscriptions = vi.fn();
const mockGetSummary = vi.fn();
const mockListVideos = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    listSubscriptions: (...args: unknown[]) => mockListSubscriptions(...args),
    getSubscriptionSummary: (...args: unknown[]) => mockGetSummary(...args),
    listSubscriptionVideos: (...args: unknown[]) => mockListVideos(...args),
  };
});

// One translator per namespace, as next-intl returns: the shared setup builds
// a new function per call, and an effect keyed on `t` then re-runs forever.
vi.mock("next-intl", async () => {
  const en = (await import("@/messages/en.json")).default as Record<string, unknown>;
  const lookup = (path: string): unknown =>
    path.split(".").reduce<unknown>(
      (node, part) =>
        typeof node === "object" && node !== null
          ? (node as Record<string, unknown>)[part]
          : undefined,
      en,
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
    useLocale: () => "en",
    NextIntlClientProvider: ({ children }: { children: React.ReactNode }) => children,
  };
});

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

// A stable router object, as next/navigation's is: a fresh one per render
// re-fires every effect that depends on it.
const router = vi.hoisted(() => ({ push: () => {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

function makeSubscription(overrides: Partial<Subscription>): Subscription {
  return {
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
    display_title: null,
    display_mode: "library",
    ...overrides,
  };
}

function makeSummary(overrides: Partial<SubscriptionSummary> = {}): SubscriptionSummary {
  return {
    total: 2, paused: 0, syncing: 0, healthy: 1, attention: 1,
    imported_count: 1, failed_count: 2,
    ...overrides,
  };
}

function video(
  subscription_id: number,
  item_id: string,
  status: SubscriptionVideo["status"],
  error_kind: string | null,
): SubscriptionVideo {
  return {
    subscription_id,
    item_id,
    status,
    error_kind,
    file_id: status === "imported" ? `f_${item_id}` : null,
    first_seen_at: "2026-05-01T00:00:00",
    last_attempted_at: null,
    title: item_id,
    thumbnail_path: null,
    channel: null,
    published_at: null,
    source_url: null,
  } as SubscriptionVideo;
}

const VIDEOS: Record<number, SubscriptionVideo[]> = {
  1: [
    video(1, "q1", "failed", "retry_pending"),
    video(1, "q2", "failed", "upcoming"),
    video(1, "q3", "failed", "permanent"),
    video(1, "q4", "failed", "dismissed"),
    video(1, "q5", "imported", "rate_limited"),
  ],
  2: [
    video(2, "a1", "failed", "retry_pending"),
    video(2, "a2", "failed", "fetch_failed"),
    video(2, "a3", "failed", null),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  mockListSubscriptions.mockResolvedValue([
    makeSubscription({ id: 1, source_ref: "UCquiet", display_title: "Quiet" }),
    makeSubscription({ id: 2, source_ref: "UCloud", display_title: "Loud" }),
  ]);
  mockGetSummary.mockResolvedValue(makeSummary());
  mockListVideos.mockImplementation((_drive: string, id: number) =>
    Promise.resolve(VIDEOS[id] ?? []),
  );
});

describe("SPEC-ADDON-028 I6 dashboard Needs attention", () => {
  it("counts only needs-attention rows on the card and in the status pill", async () => {
    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);

    const loud = await screen.findByTestId("subscription-card-2");
    await waitFor(() => expect(within(loud).getByText("2 failed")).toBeInTheDocument());
    expect(within(loud).getByText("Needs attention")).toBeInTheDocument();

    const quiet = screen.getByTestId("subscription-card-1");
    expect(quiet.textContent ?? "").not.toMatch(/[1-9]\d* failed/);
    expect(within(quiet).queryByText("Needs attention")).not.toBeInTheDocument();
  });

  it("the attention filter keeps only subscriptions with needs-attention rows", async () => {
    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await screen.findByTestId("subscription-card-1");
    await waitFor(() => expect(mockListVideos).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByTestId("filter-attention"));

    await waitFor(() => {
      expect(screen.queryByTestId("subscription-card-1")).not.toBeInTheDocument();
      expect(screen.getByTestId("subscription-card-2")).toBeInTheDocument();
    });
  });
});
