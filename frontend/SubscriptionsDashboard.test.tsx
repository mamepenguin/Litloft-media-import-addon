/**
 * Integration-style tests for SubscriptionsDashboard.
 *
 * Mocks the api module so we can assert the orchestration: fetch
 * sequence, summary header rendering, filter chip behaviour, card
 * counts, and detail panel open/close.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
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
    display_title: null,
    ...overrides,
  };
}

function makeSummary(overrides: Partial<SubscriptionSummary> = {}): SubscriptionSummary {
  return {
    total: 0, paused: 0, syncing: 0, healthy: 0, attention: 0,
    imported_count: 0, failed_count: 0,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListSubscriptions.mockResolvedValue([]);
  mockGetSummary.mockResolvedValue(makeSummary());
  mockListVideos.mockResolvedValue([] as SubscriptionVideo[]);
});

describe("SubscriptionsDashboard", () => {
  it("renders empty state when no subscriptions", async () => {
    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });
  });

  it("hides summary header when total=0", async () => {
    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dashboard-summary")).not.toBeInTheDocument();
  });

  it("renders summary header with imported and attention counts", async () => {
    mockListSubscriptions.mockResolvedValue([
      makeSubscription({ id: 1, display_title: "Fireship" }),
    ]);
    mockGetSummary.mockResolvedValue(makeSummary({
      total: 1, imported_count: 12, attention: 1, healthy: 0,
    }));

    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-summary")).toHaveTextContent("1 件の購読");
      expect(screen.getByTestId("dashboard-summary")).toHaveTextContent("12 件取り込み済み");
      expect(screen.getByTestId("summary-attention")).toBeInTheDocument();
    });
  });

  it("renders one card per subscription", async () => {
    mockListSubscriptions.mockResolvedValue([
      makeSubscription({ id: 1, display_title: "A" }),
      makeSubscription({ id: 2, source_ref: "UCb", display_title: "B" }),
    ]);
    mockGetSummary.mockResolvedValue(makeSummary({ total: 2 }));

    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("subscription-card-1")).toBeInTheDocument();
      expect(screen.getByTestId("subscription-card-2")).toBeInTheDocument();
    });
  });

  it("filters paused subscriptions when filter chip is active", async () => {
    mockListSubscriptions.mockResolvedValue([
      makeSubscription({ id: 1, is_enabled: true, display_title: "Active" }),
      makeSubscription({ id: 2, is_enabled: false, display_title: "Paused" }),
    ]);
    mockGetSummary.mockResolvedValue(makeSummary({ total: 2, paused: 1 }));

    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("subscription-card-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("filter-paused"));

    await waitFor(() => {
      expect(screen.queryByTestId("subscription-card-1")).not.toBeInTheDocument();
      expect(screen.getByTestId("subscription-card-2")).toBeInTheDocument();
    });
  });

  it("opens the detail panel on card click", async () => {
    mockListSubscriptions.mockResolvedValue([
      makeSubscription({ id: 1, display_title: "Fireship" }),
    ]);
    mockGetSummary.mockResolvedValue(makeSummary({ total: 1 }));

    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("subscription-card-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("subscription-card-1"));
    await waitFor(() => {
      expect(screen.getByTestId("detail-panel")).toBeInTheDocument();
    });
  });

  it("clicking the attention summary jumps to the attention filter", async () => {
    mockListSubscriptions.mockResolvedValue([
      makeSubscription({ id: 1, display_title: "X" }),
    ]);
    mockGetSummary.mockResolvedValue(makeSummary({
      total: 1, attention: 1,
    }));
    mockListVideos.mockResolvedValue([
      {
        subscription_id: 1,
        item_id: "vid",
        status: "failed",
        error_kind: "rate_limited",
        file_id: null,
        first_seen_at: "2026-05-01T00:00:00",
        last_attempted_at: null,
        title: null,
        thumbnail_path: null,
        channel: null,
        published_at: null,
      },
    ]);

    render(<SubscriptionsDashboard drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("summary-attention")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("summary-attention"));

    await waitFor(() => {
      // Filter chip "attention" should now be the active one.
      const chip = screen.getByTestId("filter-attention");
      expect(chip.className).toContain("border-accent");
    });
  });
});
