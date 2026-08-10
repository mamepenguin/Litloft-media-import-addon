/**
 * Unit tests for SubscriptionDetailPanel — folder edit (C-2) and
 * backfill UI (C-3).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import SubscriptionDetailPanel from "@/addons/media_import/SubscriptionDetailPanel";
import type { Subscription, SubscriptionVideo } from "@/addons/media_import/api";

const mockPatch = vi.fn();
const mockListVideos = vi.fn();
const mockExtendBackfill = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    patchSubscription: (...args: unknown[]) => mockPatch(...args),
    listSubscriptionVideos: (...args: unknown[]) => mockListVideos(...args),
    extendBackfill: (...args: unknown[]) => mockExtendBackfill(...args),
    deleteSubscription: vi.fn(),
    syncSubscription: vi.fn(),
    refreshSubscriptionMetadata: vi.fn(),
    subscriptionAvatarUrl: (id: number) => `/avatar/${id}`,
  };
});

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

function makeSubscription(overrides: Partial<Subscription> = {}): Subscription {
  return {
    id: 1,
    provider: "youtube",
    source_kind: "channel",
    source_ref: "UCabc",
    drive: "d",
    folder_path: "YouTube/test",
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

const defaultProps = {
  drive: "d",
  subscription: makeSubscription(),
  onClose: vi.fn(),
  onChanged: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  mockListVideos.mockResolvedValue([] as SubscriptionVideo[]);
  mockPatch.mockResolvedValue(makeSubscription());
  mockExtendBackfill.mockResolvedValue({ status: "queued" });
});

describe("SubscriptionDetailPanel — folder edit", () => {
  it("shows folder path and edit button", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByText("読み込み中...")).not.toBeInTheDocument());
    expect(screen.getByText("/YouTube/test")).toBeInTheDocument();
    expect(screen.getByTestId("folder-edit")).toBeInTheDocument();
  });

  it("opens inline input on edit click", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("folder-edit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("folder-edit"));
    expect(screen.getByTestId("folder-input")).toBeInTheDocument();
    expect(screen.getByTestId("folder-save")).toBeInTheDocument();
    expect(screen.getByTestId("folder-cancel")).toBeInTheDocument();
  });

  it("save button is disabled when value unchanged", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("folder-edit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("folder-edit"));
    expect(screen.getByTestId("folder-save")).toBeDisabled();
  });

  it("calls patchSubscription with new folder_path on save", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("folder-edit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("folder-edit"));
    fireEvent.change(screen.getByTestId("folder-input"), {
      target: { value: "YouTube/new" },
    });
    fireEvent.click(screen.getByTestId("folder-save"));
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("d", 1, { folder_path: "YouTube/new" }),
    );
    expect(screen.queryByTestId("folder-input")).not.toBeInTheDocument();
  });

  it("cancel restores original value and closes input", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("folder-edit")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("folder-edit"));
    fireEvent.change(screen.getByTestId("folder-input"), {
      target: { value: "YouTube/other" },
    });
    fireEvent.click(screen.getByTestId("folder-cancel"));
    expect(screen.queryByTestId("folder-input")).not.toBeInTheDocument();
    expect(screen.getByText("/YouTube/test")).toBeInTheDocument();
  });
});

describe("SubscriptionDetailPanel — backfill UI", () => {
  it("renders backfill row with count input and button", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("backfill-row")).toBeInTheDocument());
    expect(screen.getByTestId("backfill-count")).toBeInTheDocument();
    expect(screen.getByTestId("backfill-button")).toBeInTheDocument();
  });

  it("calls extendBackfill with drive, id, and count on click", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("backfill-button")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("backfill-count"), {
      target: { value: "30" },
    });
    fireEvent.click(screen.getByTestId("backfill-button"));
    await waitFor(() =>
      expect(mockExtendBackfill).toHaveBeenCalledWith("d", 1, 30),
    );
  });

  it("shows queued message after successful backfill", async () => {
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("backfill-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("backfill-button"));
    await waitFor(() =>
      expect(screen.getByTestId("backfill-message")).toBeInTheDocument(),
    );
  });

  it("shows error message when backfill fails", async () => {
    mockExtendBackfill.mockRejectedValue(new Error("network error"));
    render(<SubscriptionDetailPanel {...defaultProps} />);
    await waitFor(() => expect(screen.queryByTestId("backfill-button")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("backfill-button"));
    await waitFor(() =>
      expect(screen.getByTestId("backfill-message")).toBeInTheDocument(),
    );
  });
});
