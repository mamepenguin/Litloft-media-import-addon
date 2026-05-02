/**
 * Page-level test for Media Import subscription URL branching.
 *
 * Verifies that the URL kind detected by ``resolveSubscriptionUrl``
 * swaps the UI between single-import (``createLoft``) and subscription
 * (``createSubscription`` + ``syncSubscription``) flows.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, fireEvent, waitFor, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  // The Page is mounted at /drive/{name}/addons/media_import; the URL is
  // the single source of truth for the drive scope, so useParams must
  // resolve to the test's drive name. No more getDrives() / dropdown.
  useParams: () => ({ name: "media" }),
}));

vi.mock("@/lib/api", () => ({
  getFolders: vi.fn(async () => []),
}));

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<
    typeof import("@/addons/media_import/api")
  >("@/addons/media_import/api");
  return {
    ...actual,
    createLoft: vi.fn(),
    createSubscription: vi.fn(),
    syncSubscription: vi.fn(),
    resolveSubscriptionUrl: vi.fn(),
  };
});

vi.mock(
  "@/addons/media_import/players/registerMediaImportPlayers",
  () => ({}),
);

import MediaImportPage from "@/addons/media_import/Page";
import * as mediaApi from "@/addons/media_import/api";

const _UC = "UCabcdefghijklmnopqrstuv";

beforeEach(() => {
  vi.clearAllMocks();
});

async function settleResolveDebounce() {
  // Page debounces resolveSubscriptionUrl by 400ms; wait inside act()
  // so the post-debounce setKind state update is captured.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 450));
  });
}

describe("MediaImportPage URL branching", () => {
  it("renders single-import mode for video URLs", async () => {
    vi.mocked(mediaApi.resolveSubscriptionUrl).mockResolvedValue({
      kind: "video", provider: "youtube", ref: "abc",
    });

    render(<MediaImportPage />);

    const urlInput = screen.getByPlaceholderText("https://...");
    fireEvent.change(urlInput, {
      target: { value: "https://www.youtube.com/watch?v=abc" },
    });

    await settleResolveDebounce();

    await waitFor(() =>
      expect(screen.queryByTestId("subscription-fields")).toBeNull(),
    );
    expect(screen.getByText("Import")).toBeTruthy();
  });

  it("renders subscription fields for channel URLs", async () => {
    vi.mocked(mediaApi.resolveSubscriptionUrl).mockResolvedValue({
      kind: "channel", provider: "youtube", ref: _UC,
    });

    render(<MediaImportPage />);

    fireEvent.change(screen.getByPlaceholderText("https://..."), {
      target: { value: `https://www.youtube.com/channel/${_UC}` },
    });

    await settleResolveDebounce();

    await waitFor(() =>
      expect(screen.queryByTestId("subscription-fields")).toBeTruthy(),
    );
    expect(screen.getByText("Subscribe")).toBeTruthy();
  });

  it("submits via createSubscription + syncSubscription for channels", async () => {
    vi.mocked(mediaApi.resolveSubscriptionUrl).mockResolvedValue({
      kind: "channel", provider: "youtube", ref: _UC,
    });
    vi.mocked(mediaApi.createSubscription).mockResolvedValue({
      id: 7,
      provider: "youtube",
      source_kind: "channel",
      source_ref: _UC,
      drive: "media",
      folder_path: "",
      title: null,
      is_enabled: true,
      cooldown_minutes: 60,
      include_no_transcript: false,
      last_synced_at: null,
      cooldown_until: null,
      created_at: "2026-05-01T00:00:00",
      running: false,
    });
    vi.mocked(mediaApi.syncSubscription).mockResolvedValue({
      status: "queued",
    });

    render(<MediaImportPage />);

    fireEvent.change(screen.getByPlaceholderText("https://..."), {
      target: { value: `https://www.youtube.com/channel/${_UC}` },
    });
    await settleResolveDebounce();
    await waitFor(() =>
      expect(screen.queryByTestId("subscription-fields")).toBeTruthy(),
    );

    fireEvent.click(screen.getByText("Subscribe"));

    await waitFor(() =>
      expect(mediaApi.createSubscription).toHaveBeenCalledOnce(),
    );
    expect(mediaApi.createSubscription).toHaveBeenCalledWith(
      expect.objectContaining({
        url: `https://www.youtube.com/channel/${_UC}`,
        drive: "media",
        include_no_transcript: false,
      }),
    );
    // syncSubscription is called with the URL-scoped drive, the
    // subscription id and the default backfill (15) shown in the UI.
    expect(mediaApi.syncSubscription).toHaveBeenCalledWith("media", 7, 15);
    expect(mediaApi.createLoft).not.toHaveBeenCalled();
  });

  it("submits via createLoft for single-video URLs", async () => {
    vi.mocked(mediaApi.resolveSubscriptionUrl).mockResolvedValue({
      kind: "video", provider: "youtube", ref: "abc",
    });
    vi.mocked(mediaApi.createLoft).mockResolvedValue({
      file_id: "abc12345",
      filename: "Some Video.loft",
    });

    render(<MediaImportPage />);

    fireEvent.change(screen.getByPlaceholderText("https://..."), {
      target: { value: "https://www.youtube.com/watch?v=abc" },
    });
    await settleResolveDebounce();
    await waitFor(() =>
      expect(screen.getByText("Import")).toBeTruthy(),
    );

    fireEvent.click(screen.getByText("Import"));

    await waitFor(() =>
      expect(mediaApi.createLoft).toHaveBeenCalledOnce(),
    );
    expect(mediaApi.createSubscription).not.toHaveBeenCalled();
    expect(mediaApi.syncSubscription).not.toHaveBeenCalled();
  });
});
