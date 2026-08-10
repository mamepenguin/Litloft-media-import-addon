/**
 * Subscription display mode: the create path, the edit path, and the
 * field itself.
 *
 * Spec: `docs/superpowers/specs/2026-08-10-media-import-watch-surface.md`
 * §3.2. The rule these all serve is that `library` is where a
 * subscription starts and stays until the user moves it — importing has
 * never implied an intent to watch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import Composer from "@/addons/media_import/Composer";
import DisplayModeField from "@/addons/media_import/DisplayModeField";
import SubscriptionDetailPanel from "@/addons/media_import/SubscriptionDetailPanel";
import type { Subscription } from "@/addons/media_import/api";
import { _resetMemoryForTests } from "@/addons/media_import/lib/smartFolderMemory";

const mockResolveUrl = vi.fn();
const mockCreateSubscription = vi.fn();
const mockSyncSubscription = vi.fn();
const mockPatchSubscription = vi.fn();
const mockListVideos = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual =
    await vi.importActual<typeof import("@/addons/media_import/api")>(
      "@/addons/media_import/api",
    );
  return {
    ...actual,
    resolveSubscriptionUrl: (...args: unknown[]) => mockResolveUrl(...args),
    createLoft: vi.fn(),
    createSubscription: (...args: unknown[]) =>
      mockCreateSubscription(...args),
    syncSubscription: (...args: unknown[]) => mockSyncSubscription(...args),
    patchSubscription: (...args: unknown[]) => mockPatchSubscription(...args),
    listSubscriptionVideos: (...args: unknown[]) => mockListVideos(...args),
    deleteSubscription: vi.fn(),
    dismissSubscriptionVideo: vi.fn(),
    extendBackfill: vi.fn(),
    refreshSubscriptionMetadata: vi.fn(),
    retrySubscriptionVideo: vi.fn(),
    subscriptionAvatarUrl: (id: number) => `/avatar/${id}`,
  };
});

vi.mock("@/lib/api", () => ({
  getFolders: vi.fn().mockResolvedValue([]),
  getFolderTree: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

function makeSubscription(
  overrides: Partial<Subscription> = {},
): Subscription {
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
    display_mode: "library",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockResolveUrl.mockResolvedValue({
    kind: "unknown",
    provider: null,
    ref: null,
  });
  mockListVideos.mockResolvedValue([]);
});

afterEach(() => {
  _resetMemoryForTests();
});

describe("DisplayModeField", () => {
  it("offers the three modes as a labelled radio group", () => {
    render(
      <DisplayModeField name="t" value="library" onChange={() => {}} />,
    );
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(screen.getByLabelText(/Library only/)).toBeChecked();
    expect(screen.getByLabelText(/Show in recent videos/)).not.toBeChecked();
    expect(screen.getByLabelText(/Regular source/)).not.toBeChecked();
  });

  it("reports the chosen mode", () => {
    const onChange = vi.fn();
    render(<DisplayModeField name="t" value="library" onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/Regular source/));
    expect(onChange).toHaveBeenCalledWith("regular");
  });
});

describe("Composer display mode", () => {
  async function typeChannelUrl() {
    mockResolveUrl.mockResolvedValue({
      kind: "channel",
      provider: "youtube",
      ref: "UCabc",
    });
    mockCreateSubscription.mockResolvedValue({ id: 7 });
    mockSyncSubscription.mockResolvedValue({ status: "queued" });

    render(<Composer drive="d" initialExpanded onCreated={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("https://..."), {
      target: { value: "https://www.youtube.com/channel/UCabc" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("composer-display-mode")).toBeInTheDocument(),
    );
  }

  it("creates subscriptions as library by default", async () => {
    await typeChannelUrl();
    expect(screen.getByLabelText(/Library only/)).toBeChecked();

    fireEvent.click(screen.getByTestId("composer-submit"));
    await waitFor(() =>
      expect(mockCreateSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ display_mode: "library" }),
      ),
    );
  });

  it("sends the mode the user opted into", async () => {
    await typeChannelUrl();
    fireEvent.click(screen.getByLabelText(/Show in recent videos/));
    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() =>
      expect(mockCreateSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ display_mode: "feed" }),
      ),
    );
  });

  it("does not offer the mode for one-off video imports", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "video",
      provider: "youtube",
      ref: "abc",
    });
    render(<Composer drive="d" initialExpanded onCreated={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("https://..."), {
      target: { value: "https://youtu.be/abc" },
    });
    await waitFor(() =>
      expect(screen.getByTestId("composer-submit")).toHaveTextContent(
        "Import",
      ),
    );
    // A single import is library material by definition — there is no
    // subscription to surface (spec §2.3).
    expect(screen.queryByTestId("composer-display-mode")).toBeNull();
  });
});

describe("SubscriptionDetailPanel display mode", () => {
  it("patches only the mode, leaving import settings untouched", async () => {
    const sub = makeSubscription();
    mockPatchSubscription.mockResolvedValue(
      makeSubscription({ display_mode: "regular" }),
    );

    render(
      <SubscriptionDetailPanel
        drive="d"
        subscription={sub}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    fireEvent.click(screen.getByLabelText(/Regular source/));

    await waitFor(() =>
      expect(mockPatchSubscription).toHaveBeenCalledWith("d", 1, {
        display_mode: "regular",
      }),
    );
  });

  it("shows the mode the subscription is currently in", () => {
    render(
      <SubscriptionDetailPanel
        drive="d"
        subscription={makeSubscription({ display_mode: "feed" })}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByLabelText(/Show in recent videos/)).toBeChecked();
  });
});
