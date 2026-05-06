/**
 * Tests for the URL Composer component.
 *
 * Covers: collapse/expand, URL classification side effects, smart-folder
 * default restoration, submit dispatch (single import vs subscription),
 * error rendering, and folder memory write-back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import Composer from "@/addons/media_import/Composer";
import {
  _resetMemoryForTests,
  rememberFolder,
} from "@/addons/media_import/lib/smartFolderMemory";

const mockResolveUrl = vi.fn();
const mockCreateLoft = vi.fn();
const mockCreateSubscription = vi.fn();
const mockSyncSubscription = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    resolveSubscriptionUrl: (...args: unknown[]) => mockResolveUrl(...args),
    createLoft: (...args: unknown[]) => mockCreateLoft(...args),
    createSubscription: (...args: unknown[]) => mockCreateSubscription(...args),
    syncSubscription: (...args: unknown[]) => mockSyncSubscription(...args),
  };
});

const mockGetFolders = vi.fn().mockResolvedValue([]);
vi.mock("@/lib/api", () => ({
  getFolders: (...args: unknown[]) => mockGetFolders(...args),
}));

function makeMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => { data.set(k, v); },
    removeItem: (k: string) => { data.delete(k); },
    key: (i: number) => Array.from(data.keys())[i] ?? null,
  };
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    value: makeMemoryStorage(),
    configurable: true,
  });
  vi.clearAllMocks();
  mockResolveUrl.mockResolvedValue({ kind: "unknown", provider: null, ref: null });
  mockGetFolders.mockResolvedValue([]);
});

afterEach(() => {
  _resetMemoryForTests();
});

describe("Composer collapse / expand", () => {
  it("renders collapsed when initialExpanded=false", () => {
    render(<Composer drive="d" initialExpanded={false} onCreated={() => {}} />);
    expect(screen.getByTestId("composer-collapsed")).toBeInTheDocument();
    expect(screen.queryByTestId("composer-expanded")).not.toBeInTheDocument();
  });

  it("expands when the user clicks the affordance", () => {
    render(<Composer drive="d" initialExpanded={false} onCreated={() => {}} />);
    fireEvent.click(screen.getByTestId("composer-expand"));
    expect(screen.getByTestId("composer-expanded")).toBeInTheDocument();
  });

  it("renders expanded when initialExpanded=true", () => {
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    expect(screen.getByTestId("composer-expanded")).toBeInTheDocument();
  });

  it("collapses again on the Collapse button", () => {
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    fireEvent.click(screen.getByTestId("composer-collapse"));
    expect(screen.getByTestId("composer-collapsed")).toBeInTheDocument();
  });
});

describe("Composer URL classification", () => {
  it("renders 'Single video' hint when kind=video", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "video", provider: "youtube", ref: "abc",
    });
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://youtu.be/abc" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-url-hint")).toHaveTextContent(
        "Single video",
      );
    });
  });

  it("switches CTA to 'Subscribe' for channel URLs", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "channel", provider: "youtube", ref: "UCabc",
    });
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://www.youtube.com/channel/UCabc" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-submit")).toHaveTextContent(
        "Subscribe",
      );
    });
  });
});

describe("Composer smart-folder memory", () => {
  it("restores last folder for the same (drive, provider, kind)", async () => {
    rememberFolder("d", "youtube", "channel", "videos/yt");
    mockResolveUrl.mockResolvedValue({
      kind: "channel", provider: "youtube", ref: "UCabc",
    });

    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://www.youtube.com/channel/UCabc" } },
    );

    await waitFor(() => {
      expect(screen.getByTestId("composer-folder-toggle")).toHaveTextContent(
        "/videos/yt",
      );
    });
  });
});

describe("Composer submit dispatch", () => {
  it("dispatches createLoft for unknown URLs", async () => {
    mockResolveUrl.mockResolvedValue({ kind: "unknown", provider: null, ref: null });
    mockCreateLoft.mockResolvedValue({ file_id: "f1", filename: "x.loft" });

    const onCreated = vi.fn();
    render(<Composer drive="d" initialExpanded={true} onCreated={onCreated} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://example.com/video" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-url-hint")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() => {
      expect(mockCreateLoft).toHaveBeenCalledWith(
        "https://example.com/video", "d", "",
      );
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("dispatches createSubscription + syncSubscription for channel URLs", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "channel", provider: "youtube", ref: "UCabc",
    });
    mockCreateSubscription.mockResolvedValue({ id: 7 });
    mockSyncSubscription.mockResolvedValue({ status: "queued" });

    const onCreated = vi.fn();
    render(<Composer drive="d" initialExpanded={true} onCreated={onCreated} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://www.youtube.com/channel/UCabc" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-submit")).toHaveTextContent(
        "Subscribe",
      );
    });

    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() => {
      expect(mockCreateSubscription).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "https://www.youtube.com/channel/UCabc",
          drive: "d",
        }),
      );
      expect(mockSyncSubscription).toHaveBeenCalledWith("d", 7, 15);
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("memorises the destination folder after a successful submit", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "channel", provider: "youtube", ref: "UCabc",
    });
    mockCreateSubscription.mockResolvedValue({ id: 7 });
    mockSyncSubscription.mockResolvedValue({ status: "queued" });

    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://www.youtube.com/channel/UCabc" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-submit")).toHaveTextContent(
        "Subscribe",
      );
    });

    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() => {
      // localStorage entry written with the empty default folder
      const raw = window.localStorage.getItem(
        "media_import.last_folder_v1",
      );
      expect(raw).not.toBeNull();
      const parsed = JSON.parse(raw!);
      expect(parsed["d|youtube|channel"]).toBe("");
    });
  });

  it("renders error message when createLoft throws", async () => {
    mockResolveUrl.mockResolvedValue({ kind: "unknown", provider: null, ref: null });
    mockCreateLoft.mockRejectedValue(new Error("Boom"));

    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://example.com/v" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-url-hint")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("composer-error")).toHaveTextContent("Boom");
    });
  });
});
