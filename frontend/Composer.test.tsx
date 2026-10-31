/**
 * Tests for the URL Composer component.
 *
 * Covers: collapse/expand, URL classification side effects, smart-folder
 * default restoration, submit dispatch (single import vs subscription),
 * error rendering, and folder memory write-back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import Composer from "@/addons/media_import/Composer";
import { COMPOSITION_GRACE_MS } from "@/lib/ime";
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
  getFolderTree: vi.fn().mockResolvedValue([]),
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
      expect(screen.getByText("/videos/yt")).toBeInTheDocument();
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
        "https://example.com/video", "d", "", "manual",
      );
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("persists and sends the selected STT mode for single imports", async () => {
    mockResolveUrl.mockResolvedValue({
      kind: "video", provider: "youtube", ref: "abc",
    });
    mockCreateLoft.mockResolvedValue({ file_id: "f1", filename: "x.loft" });

    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    fireEvent.change(
      screen.getByPlaceholderText("https://..."),
      { target: { value: "https://youtu.be/abc" } },
    );
    await waitFor(() => {
      expect(screen.getByTestId("composer-stt-mode")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("composer-stt-mode-missing_captions"));
    expect(screen.getByTestId("composer-stt-mode-missing_captions")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("group", { name: "Speech-to-text" })).toContainElement(
      screen.getByTestId("composer-stt-mode-missing_captions"),
    );
    expect(
      screen.getAllByRole("button", { pressed: true }).filter((b) => b.dataset.testid?.startsWith("composer-stt-mode-")),
    ).toHaveLength(1);
    fireEvent.click(screen.getByTestId("composer-submit"));

    await waitFor(() => {
      expect(mockCreateLoft).toHaveBeenCalledWith(
        "https://youtu.be/abc", "d", "", "missing_captions",
      );
    });
    expect(window.localStorage.getItem("media_import.stt_mode_v1")).toBe(
      "missing_captions",
    );
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

describe("Composer submit before classification finishes", () => {
  const CHANNEL = "https://www.youtube.com/channel/UCabc";
  const VIDEO = "https://youtu.be/abc";
  const CHANNEL_RES = { kind: "channel", provider: "youtube", ref: "UCabc" };
  const VIDEO_RES = { kind: "video", provider: "youtube", ref: "abc" };

  const urlBox = () => screen.getByPlaceholderText("https://...");
  const type = (value: string) => fireEvent.change(urlBox(), { target: { value } });
  const settle = () => act(async () => {
    await vi.advanceTimersByTimeAsync(400);
  });
  const enter = () => fireEvent.keyDown(urlBox(), { key: "Enter" });

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    mockCreateLoft.mockResolvedValue({ file_id: "f1", filename: "x.loft" });
    mockCreateSubscription.mockResolvedValue({ id: 7 });
    mockSyncSubscription.mockResolvedValue({ status: "queued" });
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    { id: "debounce pending", prior: false, wait: "none", resolves: CHANNEL_RES, subscribes: true },
    { id: "request in flight", prior: false, wait: "debounce", resolves: CHANNEL_RES, subscribes: true },
    { id: "finished for this URL", prior: false, wait: "settle", resolves: CHANNEL_RES, subscribes: true },
    { id: "finished for a previous URL", prior: true, wait: "none", resolves: CHANNEL_RES, subscribes: true },
    { id: "failed before submit", prior: false, wait: "settle", resolves: null, subscribes: false },
    { id: "failed at submit", prior: false, wait: "none", resolves: null, subscribes: false },
  ] as const)("$id", async ({ prior, wait, resolves, subscribes }) => {
    const answer = (u: string) => {
      if (resolves === null) return Promise.reject(new Error("down"));
      return Promise.resolve(u === VIDEO ? VIDEO_RES : resolves);
    };
    let release: () => void = () => {};
    mockResolveUrl.mockImplementation((u: string) => {
      if (wait === "debounce" && mockResolveUrl.mock.calls.length === 1) {
        return new Promise<void>((r) => { release = r; }).then(() => answer(u));
      }
      return answer(u);
    });
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);

    if (prior) {
      type(VIDEO);
      await settle();
      expect(mockResolveUrl).toHaveBeenCalledTimes(1);
    }
    type(CHANNEL);
    if (wait !== "none") await settle();
    enter();
    release();

    await waitFor(() => {
      expect(mockCreateSubscription).toHaveBeenCalledTimes(subscribes ? 1 : 0);
      expect(mockCreateLoft).toHaveBeenCalledTimes(subscribes ? 0 : 1);
    });
  });

  it("does not import twice on a second Enter during the wait", async () => {
    mockResolveUrl.mockResolvedValue(CHANNEL_RES);
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    type(CHANNEL);

    enter();
    enter();

    await waitFor(() => expect(mockCreateSubscription).toHaveBeenCalledTimes(1));
    expect(mockSyncSubscription).toHaveBeenCalledTimes(1);
  });

  it("does nothing on Enter with an empty input", () => {
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    enter();
    expect(mockResolveUrl).not.toHaveBeenCalled();
    expect(mockCreateLoft).not.toHaveBeenCalled();
    expect(mockCreateSubscription).not.toHaveBeenCalled();
  });

  it("uses the remembered folder when the preview has not finished", async () => {
    rememberFolder("d", "youtube", "channel", "videos/yt");
    mockResolveUrl.mockResolvedValue(CHANNEL_RES);
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    type(CHANNEL);

    enter();

    await waitFor(() =>
      expect(mockCreateSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ folder_path: "videos/yt" }),
      ),
    );
  });
});

describe("Composer IME composition", () => {
  const URL_TYPED = "https://example.com/動画";
  let now: ReturnType<typeof vi.spyOn> | null = null;

  afterEach(() => {
    now?.mockRestore();
    now = null;
  });

  function renderWithConvertedUrl() {
    mockCreateLoft.mockResolvedValue({ file_id: "f1", filename: "x.loft" });
    render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
    const input = screen.getByPlaceholderText("https://...");
    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: URL_TYPED } });
    fireEvent.compositionEnd(input, { data: "動画" });
    return input;
  }

  it("does not import on the Enter that confirms a conversion", () => {
    now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const input = renderWithConvertedUrl();
    now.mockReturnValue(1_000_000 + COMPOSITION_GRACE_MS - 1);

    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });

    expect(mockCreateLoft).not.toHaveBeenCalled();
    expect(mockCreateSubscription).not.toHaveBeenCalled();
  });

  it("does not import on an Enter the IME still owns", () => {
    now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const input = renderWithConvertedUrl();
    now.mockReturnValue(1_000_000 + COMPOSITION_GRACE_MS);
    fireEvent.compositionStart(input);

    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });

    expect(mockCreateLoft).not.toHaveBeenCalled();
    expect(mockCreateSubscription).not.toHaveBeenCalled();
  });

  it("imports once on an Enter pressed after the grace window", async () => {
    now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const input = renderWithConvertedUrl();
    now.mockReturnValue(1_000_000 + COMPOSITION_GRACE_MS);

    fireEvent.keyDown(input, { key: "Enter", keyCode: 13 });

    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockCreateLoft.mock.calls[0][0]).toBe(URL_TYPED);
  });
});
