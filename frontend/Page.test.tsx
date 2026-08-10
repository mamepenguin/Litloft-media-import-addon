/**
 * Watch / Manage navigation.
 *
 * Spec §3.1: Manage stays the useful landing view when nothing has been
 * surfaced, and Watch never implies that importing is unfinished.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import MediaImportPage from "@/addons/media_import/Page";
import type { Subscription } from "@/addons/media_import/api";

const mockListSubscriptions = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ name: "d" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/addons/media_import/api", async () => {
  const actual =
    await vi.importActual<typeof import("@/addons/media_import/api")>(
      "@/addons/media_import/api",
    );
  return {
    ...actual,
    listSubscriptions: (...args: unknown[]) => mockListSubscriptions(...args),
    listWatch: vi.fn().mockResolvedValue([]),
    listActivity: vi.fn().mockResolvedValue([]),
    getSubscriptionSummary: vi.fn().mockResolvedValue({
      total: 0,
      paused: 0,
      syncing: 0,
      healthy: 0,
      attention: 0,
      imported_count: 0,
      failed_count: 0,
    }),
    resolveSubscriptionUrl: vi
      .fn()
      .mockResolvedValue({ kind: "unknown", provider: null, ref: null }),
  };
});

vi.mock("@/lib/api", () => ({
  getFolders: vi.fn().mockResolvedValue([]),
  getFolderTree: vi.fn().mockResolvedValue([]),
  getCollections: vi.fn().mockResolvedValue([]),
  createCollection: vi.fn(),
  addCollectionItems: vi.fn(),
}));

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

function makeSubscription(mode: Subscription["display_mode"]): Subscription {
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
    display_title: "Chan",
    display_mode: mode,
  };
}

describe("MediaImportPage navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lands on Manage when nothing is surfaced", async () => {
    mockListSubscriptions.mockResolvedValue([makeSubscription("library")]);
    render(<MediaImportPage />);

    await waitFor(() =>
      expect(screen.getByTestId("composer-collapsed")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("watch-view")).toBeNull();
  });

  it("lands on Watch once a source is surfaced", async () => {
    mockListSubscriptions.mockResolvedValue([makeSubscription("feed")]);
    render(<MediaImportPage />);

    await waitFor(() =>
      expect(screen.getByTestId("watch-view")).toBeInTheDocument(),
    );
  });

  it("lets the user switch between the two views", async () => {
    mockListSubscriptions.mockResolvedValue([makeSubscription("feed")]);
    render(<MediaImportPage />);

    await waitFor(() =>
      expect(screen.getByTestId("watch-view")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(screen.queryByTestId("watch-view")).toBeNull();
    expect(screen.getByTestId("composer-collapsed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    expect(screen.getByTestId("watch-view")).toBeInTheDocument();
  });

  it("falls back to Manage when subscriptions cannot be read", async () => {
    mockListSubscriptions.mockRejectedValue(new Error("offline"));
    render(<MediaImportPage />);

    await waitFor(() =>
      expect(screen.getByTestId("composer-expanded")).toBeInTheDocument(),
    );
  });
});
