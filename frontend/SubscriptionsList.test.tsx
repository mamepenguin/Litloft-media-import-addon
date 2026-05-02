/**
 * Tests for SubscriptionsList — per-drive subscription overview with
 * sync / delete / expand controls and WS-driven Syncing badge.
 *
 * Sync is fire-and-forget: ``syncSubscription`` enqueues a worker job
 * and returns immediately. Completion lands as
 * ``subscription.sync_completed`` over WebSocket and triggers a refetch.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, fireEvent, screen, waitFor } from "@testing-library/react";

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<
    typeof import("@/addons/media_import/api")
  >("@/addons/media_import/api");
  return {
    ...actual,
    listSubscriptions: vi.fn(),
    syncSubscription: vi.fn(),
    deleteSubscription: vi.fn(),
    listSubscriptionVideos: vi.fn(async () => []),
  };
});

// Stateful WS mock: tests update mockEvents to simulate inbound events.
const mockEvents: Record<string, { event: string; data: unknown } | null> = {};
vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: (filter?: string) => mockEvents[filter ?? ""] ?? null,
}));

import SubscriptionsList from "@/addons/media_import/SubscriptionsList";
import * as api from "@/addons/media_import/api";

function fakeSub(overrides: Partial<api.Subscription> = {}): api.Subscription {
  return {
    id: 1,
    provider: "youtube",
    source_kind: "channel",
    source_ref: "UCabc",
    drive: "media",
    folder_path: "yt",
    title: "Sample Channel",
    is_enabled: true,
    cooldown_minutes: 60,
    include_no_transcript: false,
    last_synced_at: null,
    cooldown_until: null,
    created_at: "2026-05-01T00:00:00",
    running: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  for (const key of Object.keys(mockEvents)) delete mockEvents[key];
});

describe("SubscriptionsList", () => {
  it("renders the empty state when no subscriptions exist", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([]);
    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscriptions-empty")).toBeTruthy(),
    );
  });

  it("renders one row per subscription", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      fakeSub({ id: 1, title: "Channel A" }),
      fakeSub({
        id: 2,
        source_kind: "playlist",
        source_ref: "PLxyz",
        title: "Playlist B",
      }),
    ]);
    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-1")).toBeTruthy(),
    );
    expect(screen.getByTestId("subscription-row-2")).toBeTruthy();
    expect(screen.getByText("Channel A")).toBeTruthy();
    expect(screen.getByText("Playlist B")).toBeTruthy();
  });

  it("calls syncSubscription on Sync click and shows the badge", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 7 })]);
    vi.mocked(api.syncSubscription).mockResolvedValue({ status: "queued" });

    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-7")).toBeTruthy(),
    );
    expect(screen.queryByTestId("syncing-badge-7")).toBeNull();

    fireEvent.click(screen.getByTestId("sync-7"));
    await waitFor(() =>
      expect(api.syncSubscription).toHaveBeenCalledWith("media", 7),
    );
    // Optimistic Syncing badge appears even before the WS event lands.
    await waitFor(() =>
      expect(screen.getByTestId("syncing-badge-7")).toBeTruthy(),
    );
    // No automatic refetch — completion handler will refetch via WS.
    expect(api.listSubscriptions).toHaveBeenCalledTimes(1);
  });

  it("renders Syncing badge for subscriptions with running=true on load", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      fakeSub({ id: 5, running: true }),
    ]);
    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("syncing-badge-5")).toBeTruthy(),
    );
  });

  it("clears Syncing badge and refetches on subscription.sync_completed", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      fakeSub({ id: 11, running: true }),
    ]);

    const { rerender } = render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("syncing-badge-11")).toBeTruthy(),
    );
    expect(api.listSubscriptions).toHaveBeenCalledTimes(1);

    // Next refetch returns the row with running=false.
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      fakeSub({ id: 11, running: false, last_synced_at: "2026-05-01T01:00:00" }),
    ]);

    // Simulate WS event arrival.
    mockEvents["media_import.subscription.sync_completed"] = {
      event: "media_import.subscription.sync_completed",
      data: { subscription_id: 11, drive: "media", added: 3, total_new: 3 },
    };
    rerender(<SubscriptionsList drive="media" />);

    await waitFor(() =>
      expect(api.listSubscriptions).toHaveBeenCalledTimes(2),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("syncing-badge-11")).toBeNull(),
    );
  });

  it("adds Syncing badge on subscription.sync_started", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 8 })]);
    const { rerender } = render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-8")).toBeTruthy(),
    );
    expect(screen.queryByTestId("syncing-badge-8")).toBeNull();

    mockEvents["media_import.subscription.sync_started"] = {
      event: "media_import.subscription.sync_started",
      data: { subscription_id: 8, drive: "media" },
    };
    rerender(<SubscriptionsList drive="media" />);

    await waitFor(() =>
      expect(screen.getByTestId("syncing-badge-8")).toBeTruthy(),
    );
  });

  it("calls deleteSubscription after confirm", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 9 })]);
    vi.mocked(api.deleteSubscription).mockResolvedValue();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-9")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("delete-9"));
    await waitFor(() =>
      expect(api.deleteSubscription).toHaveBeenCalledWith("media", 9),
    );

    confirmSpy.mockRestore();
  });

  it("does not call deleteSubscription when confirm is cancelled", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 9 })]);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-9")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("delete-9"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(api.deleteSubscription).not.toHaveBeenCalled();

    confirmSpy.mockRestore();
  });

  it("expands a row to show items when chevron is clicked", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 3 })]);
    vi.mocked(api.listSubscriptionVideos).mockResolvedValue([]);

    render(<SubscriptionsList drive="media" />);
    const row = await waitFor(() =>
      screen.getByTestId("subscription-row-3"),
    );

    const toggle = row.querySelector("button[aria-label='Toggle items']");
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle!);

    await waitFor(() =>
      expect(api.listSubscriptionVideos).toHaveBeenCalledWith("media", 3),
    );
  });
});
