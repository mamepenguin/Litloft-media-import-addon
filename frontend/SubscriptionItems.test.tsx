/**
 * Tests for SubscriptionItems — the per-subscription expanded item list.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<
    typeof import("@/addons/media_import/api")
  >("@/addons/media_import/api");
  return {
    ...actual,
    listSubscriptionVideos: vi.fn(),
    retrySubscriptionVideo: vi.fn(),
  };
});

import SubscriptionItems from "@/addons/media_import/SubscriptionItems";
import * as api from "@/addons/media_import/api";

function video(
  overrides: Partial<api.SubscriptionVideo> = {},
): api.SubscriptionVideo {
  return {
    subscription_id: 1,
    item_id: "vid_x",
    status: "imported",
    error_kind: null,
    file_id: "f_abc",
    first_seen_at: "2026-05-01T00:00:00",
    last_attempted_at: "2026-05-01T00:00:00",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SubscriptionItems", () => {
  it("renders the empty state when no items exist", async () => {
    vi.mocked(api.listSubscriptionVideos).mockResolvedValue([]);
    render(<SubscriptionItems subscriptionId={1} />);
    await waitFor(() =>
      expect(screen.getByText("No items yet. Run a sync.")).toBeTruthy(),
    );
  });

  it("renders one row per item", async () => {
    vi.mocked(api.listSubscriptionVideos).mockResolvedValue([
      video({ item_id: "a" }),
      video({ item_id: "b", status: "failed", error_kind: "rate_limited" }),
    ]);
    render(<SubscriptionItems subscriptionId={1} />);
    await waitFor(() =>
      expect(screen.getByTestId("video-row-a")).toBeTruthy(),
    );
    expect(screen.getByTestId("video-row-b")).toBeTruthy();
  });

  it("shows a Retry button only for failed items with non-permanent error", async () => {
    vi.mocked(api.listSubscriptionVideos).mockResolvedValue([
      video({ item_id: "ok", status: "imported" }),
      video({ item_id: "rl", status: "failed", error_kind: "rate_limited" }),
      video({ item_id: "perm", status: "failed", error_kind: "permanent" }),
    ]);
    render(<SubscriptionItems subscriptionId={1} />);
    await waitFor(() =>
      expect(screen.getByTestId("video-row-ok")).toBeTruthy(),
    );
    expect(screen.queryByTestId("retry-ok")).toBeNull();
    expect(screen.queryByTestId("retry-rl")).toBeTruthy();
    // permanent failures should not be retryable — retry would just hit
    // the same upstream block.
    expect(screen.queryByTestId("retry-perm")).toBeNull();
  });

  it("calls retrySubscriptionVideo and reloads on Retry click", async () => {
    vi.mocked(api.listSubscriptionVideos).mockResolvedValue([
      video({ item_id: "rl", status: "failed", error_kind: "rate_limited" }),
    ]);
    vi.mocked(api.retrySubscriptionVideo).mockResolvedValue({
      added: 1, reused: 0, failed: 0, total_new: 1,
    });

    render(<SubscriptionItems subscriptionId={42} />);
    await waitFor(() =>
      expect(screen.getByTestId("retry-rl")).toBeTruthy(),
    );

    fireEvent.click(screen.getByTestId("retry-rl"));
    await waitFor(() =>
      expect(api.retrySubscriptionVideo).toHaveBeenCalledWith(42, "rl"),
    );
    // Items list re-loads after retry.
    expect(api.listSubscriptionVideos).toHaveBeenCalledTimes(2);
  });
});
