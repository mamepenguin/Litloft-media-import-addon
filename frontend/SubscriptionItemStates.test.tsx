/**
 * Acceptance tests for the detail panel's item groups, the source link and
 * the rate-limit notice (SPEC-ADDON-027, SPEC-ADDON-028, SPEC-ADDON-029).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import SubscriptionDetailPanel from "@/addons/media_import/SubscriptionDetailPanel";
import type { Subscription, SubscriptionVideo } from "@/addons/media_import/api";

const mockListVideos = vi.fn();
const ws = vi.hoisted(() => ({
  event: null as { event: string; data: Record<string, unknown> } | null,
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

// A stable router object, as next/navigation's is: a fresh one per render
// re-fires every effect that depends on it.
const router = vi.hoisted(() => ({ push: () => {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: (filter?: string) =>
    ws.event && (!filter || ws.event.event === filter) ? ws.event : null,
}));

function makeSubscription(overrides: Partial<Subscription> = {}): Subscription {
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
    display_title: "Test Channel",
    display_mode: "library",
    ...overrides,
  };
}

function video(
  item_id: string,
  status: SubscriptionVideo["status"],
  error_kind: string | null,
  title: string | null,
  file_id: string | null = null,
): SubscriptionVideo {
  return {
    subscription_id: 1,
    item_id,
    status,
    error_kind,
    file_id,
    first_seen_at: "2026-05-01T00:00:00",
    last_attempted_at: null,
    title,
    thumbnail_path: null,
    channel: null,
    published_at: null,
    source_url: `https://www.youtube.com/watch?v=${item_id}`,
  } as SubscriptionVideo;
}

const ROWS: SubscriptionVideo[] = [
  video("att00000001", "failed", "fetch_failed", "Attention one"),
  video("con00000001", "failed", "path_conflict", null),
  video("pen00000001", "failed", "retry_pending", "Pending one"),
  video("upc00000001", "failed", "upcoming", "Upcoming one"),
  video("prm00000001", "failed", "permanent", "Members only one"),
  video("imp00000001", "imported", "no_transcript", "Imported one", "f1"),
  video("skp00000001", "failed", "dismissed", "Skipped one"),
];

const props = {
  drive: "d",
  subscription: makeSubscription(),
  onClose: vi.fn(),
  onChanged: vi.fn(),
};

function before(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

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
  mockListVideos.mockResolvedValue(ROWS);
});

describe("SPEC-ADDON-027/028/029 detail panel item groups", () => {
  it("SPEC-ADDON-028: groups rows into Needs attention, Retrying, Unavailable, Imported, in that order", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    const attention = await screen.findByText("Needs attention (2)");
    const retrying = screen.getByText("Retrying (2)");
    const unavailable = screen.getByText("Unavailable (1)");
    const imported = screen.getByText("Imported (1)");
    expect(screen.getByText("Skipped (1)")).toBeInTheDocument();

    const a1 = screen.getByText("Attention one");
    const placeholder = screen.getByText("Untitled video");
    const p1 = screen.getByText("Pending one");
    const u1 = screen.getByText("Upcoming one");
    const x1 = screen.getByText("Members only one");
    const i1 = screen.getByText("Imported one");

    expect(before(attention, a1)).toBe(true);
    expect(before(attention, placeholder)).toBe(true);
    expect(before(a1, retrying) && before(placeholder, retrying)).toBe(true);
    expect(before(retrying, p1) && before(retrying, u1)).toBe(true);
    expect(before(p1, unavailable) && before(u1, unavailable)).toBe(true);
    expect(before(unavailable, x1)).toBe(true);
    expect(before(x1, imported) && before(imported, i1)).toBe(true);
    // Skipped rows are a count only.
    expect(screen.queryByText("Skipped one")).not.toBeInTheDocument();
  });

  it("SPEC-ADDON-028: retry-pending and upcoming rows carry their own label and hint", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("Retrying (2)");

    expect(screen.getAllByText("Retrying", { exact: true })).toHaveLength(1);
    expect(screen.getAllByText("Not started", { exact: true })).toHaveLength(1);
    expect(hasHint("Will be tried again on the next sync.")).toBe(true);
    expect(hasHint("Will be imported once the live stream or premiere starts.")).toBe(true);
  });

  it("SPEC-ADDON-027: unavailable rows carry the Unavailable label and the new hint", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("Unavailable (1)");

    expect(screen.getAllByText("Unavailable", { exact: true })).toHaveLength(1);
    expect(
      hasHint("Members-only, age-restricted, private or removed; cannot be fetched."),
    ).toBe(true);
  });

  it("SPEC-ADDON-027/028: headings and pills use the specified colour tokens", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    const retryingHeading = await screen.findByText("Retrying (2)");
    const unavailableHeading = screen.getByText("Unavailable (1)");
    const attentionHeading = screen.getByText("Needs attention (2)");

    expect(retryingHeading.closest("[class]")!.className).toContain("text-text-muted");
    expect(unavailableHeading.closest("[class]")!.className).toContain("text-text-muted");
    expect(attentionHeading.closest("[class]")!.className).toContain("text-accent-amber");

    const amberPill = screen.getByText("Retrying", { exact: true }).closest("[class]")!;
    expect(amberPill.className).toContain("bg-accent-amber/15");
    expect(amberPill.className).toContain("text-accent-amber");
    const upcomingPill = screen.getByText("Not started", { exact: true }).closest("[class]")!;
    expect(upcomingPill.className).toContain("bg-accent-amber/15");
    const grayPill = screen.getByText("Unavailable", { exact: true }).closest("[class]")!;
    expect(grayPill.className).toContain("bg-bg-elevated");
    expect(grayPill.className).toContain("text-text-muted");
  });

  it("SPEC-ADDON-027/028 I8: every row in groups 1-3 has Ignore, and Retry or Resolve", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("Retrying (2)");

    expect(screen.getAllByRole("button", { name: "Ignore" })).toHaveLength(5);
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: "Resolve…" })).toHaveLength(1);
  });

  it("SPEC-ADDON-029 I8: rows in groups 1-3 link to source_url in a new tab; imported rows do not", async () => {
    render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("Retrying (2)");

    const links = screen.getAllByRole("link", { name: "Open the source page" });
    const hrefs = links.map((a) => a.getAttribute("href")).sort();
    expect(hrefs).toEqual(
      ["att00000001", "con00000001", "pen00000001", "prm00000001", "upc00000001"]
        .map((id) => `https://www.youtube.com/watch?v=${id}`)
        .sort(),
    );
    for (const a of links) {
      expect(a.getAttribute("target")).toBe("_blank");
      const rel = (a.getAttribute("rel") ?? "").split(/\s+/);
      expect(rel).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
    }
  });
});

describe("SPEC-ADDON-028 rate-limit notice (Q3)", () => {
  const NOTICE = "YouTube is limiting requests for now. Try again later.";

  function completed(subscription_id: number, rate_limited: boolean) {
    return {
      event: "media_import.subscription.sync_completed",
      data: {
        subscription_id,
        drive: "d",
        added: 0,
        reused: 0,
        failed: 0,
        total_new: 0,
        retried: 0,
        rate_limited,
      },
    };
  }

  it("shows the notice only for this subscription's rate-limited sync and clears it on the next one", async () => {
    const { rerender } = render(<SubscriptionDetailPanel {...props} />);
    await screen.findByText("Retrying (2)");
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();

    ws.event = completed(2, true);
    rerender(<SubscriptionDetailPanel {...props} />);
    await waitFor(() => expect(mockListVideos).toHaveBeenCalled());
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();

    ws.event = completed(1, true);
    rerender(<SubscriptionDetailPanel {...props} />);
    const notice = await screen.findByText(NOTICE);
    expect(before(notice, await screen.findByText("Needs attention (2)"))).toBe(true);

    ws.event = completed(1, false);
    rerender(<SubscriptionDetailPanel {...props} />);
    await waitFor(() => expect(screen.queryByText(NOTICE)).not.toBeInTheDocument());
  });
});
