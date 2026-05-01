/**
 * Tests for SubscriptionsList — the per-drive subscription overview
 * with sync / delete / expand controls.
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
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
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
      fakeSub({ id: 2, source_kind: "playlist", title: "Playlist B" }),
    ]);
    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-1")).toBeTruthy(),
    );
    expect(screen.getByTestId("subscription-row-2")).toBeTruthy();
    expect(screen.getByText("Channel A")).toBeTruthy();
    expect(screen.getByText("Playlist B")).toBeTruthy();
  });

  it("calls syncSubscription and re-fetches the list on Sync click", async () => {
    vi.mocked(api.listSubscriptions).mockResolvedValue([fakeSub({ id: 7 })]);
    vi.mocked(api.syncSubscription).mockResolvedValue({
      added: 2, reused: 0, failed: 0, total_new: 2,
    });

    render(<SubscriptionsList drive="media" />);
    await waitFor(() =>
      expect(screen.getByTestId("subscription-row-7")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("sync-7"));
    await waitFor(() =>
      expect(api.syncSubscription).toHaveBeenCalledWith(7),
    );
    // The list re-loads after sync.
    expect(api.listSubscriptions).toHaveBeenCalledTimes(2);
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
      expect(api.deleteSubscription).toHaveBeenCalledWith(9),
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
    // give any error path a tick to surface
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
      expect(api.listSubscriptionVideos).toHaveBeenCalledWith(3),
    );
  });
});
