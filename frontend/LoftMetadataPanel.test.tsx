import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { Captions } from "lucide-react";

import { ToastProvider } from "@/components/ToastProvider";
import { resetFileAiActions, useFileAiActions } from "@/lib/fileAiActions";
import type { FileAiAction } from "@/lib/fileAiActions";
import type { LoftMetadata } from "@/addons/media_import/api";

const getLoftMetadata = vi.fn();
const generateLoftStt = vi.fn();
const refreshLoft = vi.fn();
vi.mock("@/addons/media_import/api", () => ({
  getLoftMetadata: (...args: unknown[]) => getLoftMetadata(...args),
  generateLoftStt: (...args: unknown[]) => generateLoftStt(...args),
  refreshLoft: (...args: unknown[]) => refreshLoft(...args),
}));

import LoftMetadataPanel from "@/addons/media_import/LoftMetadataPanel";
import { notifyLoftRefreshed } from "@/addons/media_import/loftRefresh";

const messages = {
  mediaImport: {
    loftMetadata: {
      generateStt: "Generate captions with speech-to-text",
      refreshFailed: "Failed to refresh metadata",
      showMore: "Show more",
      showLess: "Show less",
      sttStatus: {
        queued: "Speech-to-text queued",
        already_queued: "Speech-to-text is already queued",
        error: "Failed to queue speech-to-text",
      },
    },
  },
  captionStatus: {
    noCaptions: "No captions",
    rateLimited: "Rate limited",
    permanent: "Permanent",
    failed: "Failed to download captions",
    notAttempted: "Not attempted",
    retryHint: "Click to retry",
    retrying: "Retrying...",
  },
};

function makeMetadata(overrides: Partial<LoftMetadata> = {}): LoftMetadata {
  return {
    provider: "youtube",
    url: "https://www.youtube.com/watch?v=abc",
    description: null,
    channel: "Channel",
    published_at: null,
    language: null,
    has_captions: true,
    captions_downloaded: true,
    caption_error_kind: null,
    fetched_at: "2026-09-28T00:00:00Z",
    fetch_error: null,
    ...overrides,
  };
}

let offers: readonly FileAiAction[] = [];
function OfferReader() {
  offers = useFileAiActions("f1");
  return null;
}

function panel(fileId: string) {
  return (
    <NextIntlClientProvider locale="en" messages={messages}>
      <ToastProvider>
        <OfferReader />
        <LoftMetadataPanel fileId={fileId} drive="d" />
      </ToastProvider>
    </NextIntlClientProvider>
  );
}

function renderPanel(fileId = "f1") {
  return render(panel(fileId));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  getLoftMetadata.mockReset();
  generateLoftStt.mockReset();
  refreshLoft.mockReset();
  offers = [];
});

afterEach(() => {
  act(() => resetFileAiActions());
  vi.useRealTimers();
});

describe("LoftMetadataPanel", () => {
  it("offers transcription to the AI menu before its metadata has loaded", () => {
    getLoftMetadata.mockReturnValue(new Promise(() => {}));
    renderPanel();

    expect(offers).toHaveLength(1);
    expect(offers[0]).toMatchObject({
      id: "media_import.transcribe",
      label: "Generate captions with speech-to-text",
      icon: Captions,
      busy: false,
    });
    expect(offers[0].order).toBeGreaterThan(50);
  });

  it.each([
    ["queued", "Speech-to-text queued"],
    ["already_queued", "Speech-to-text is already queued"],
  ] as const)(
    "queues transcription once, busy only in flight, and toasts %s",
    async (status, toast) => {
      getLoftMetadata.mockResolvedValue(makeMetadata());
      const request = deferred<{ status: typeof status }>();
      generateLoftStt.mockReturnValue(request.promise);
      renderPanel();

      act(() => {
      offers[0].run();
    });
      expect(generateLoftStt).toHaveBeenCalledTimes(1);
      expect(generateLoftStt).toHaveBeenCalledWith("f1", "d");
      await waitFor(() => expect(offers[0].busy).toBe(true));

      await act(async () => request.resolve({ status }));
      expect(offers[0].busy).toBe(false);
      expect(await screen.findByRole("status")).toHaveTextContent(toast);
      expect(screen.queryByRole("alert")).toBeNull();
    },
  );

  it("reports a failed transcription request and stops being busy", async () => {
    getLoftMetadata.mockResolvedValue(makeMetadata());
    const request = deferred<never>();
    generateLoftStt.mockReturnValue(request.promise);
    renderPanel();

    act(() => {
      offers[0].run();
    });
    await waitFor(() => expect(offers[0].busy).toBe(true));
    await act(async () => request.reject(new Error("500")));

    expect(offers[0].busy).toBe(false);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to queue speech-to-text",
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("re-reads its metadata after a refresh of this file only", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getLoftMetadata.mockResolvedValue(makeMetadata());
    renderPanel();
    await screen.findByText("Channel");
    expect(getLoftMetadata).toHaveBeenCalledTimes(1);

    act(() => notifyLoftRefreshed("other"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(getLoftMetadata).toHaveBeenCalledTimes(1);

    getLoftMetadata.mockResolvedValue(makeMetadata({ channel: "Renamed" }));
    act(() => notifyLoftRefreshed("f1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(getLoftMetadata).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Renamed")).toBeInTheDocument();
  });

  it("stops listening for refreshes once unmounted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getLoftMetadata.mockResolvedValue(makeMetadata());
    const { unmount } = renderPanel();
    await screen.findByText("Channel");
    unmount();

    act(() => notifyLoftRefreshed("f1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(getLoftMetadata).toHaveBeenCalledTimes(1);
  });

  it("refreshes from the caption badge's retry", async () => {
    getLoftMetadata.mockResolvedValue(
      makeMetadata({ captions_downloaded: false }),
    );
    refreshLoft.mockResolvedValue(undefined);
    renderPanel();

    fireEvent.click(
      await screen.findByRole("button", { name: /Failed to download captions/ }),
    );
    expect(refreshLoft).toHaveBeenCalledTimes(1);
    expect(refreshLoft).toHaveBeenCalledWith("f1", "d");
    await act(async () => {});
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reports a refused retry from the caption badge", async () => {
    getLoftMetadata.mockResolvedValue(
      makeMetadata({ captions_downloaded: false }),
    );
    refreshLoft.mockRejectedValue(new Error("404"));
    renderPanel();

    fireEvent.click(
      await screen.findByRole("button", { name: /Failed to download captions/ }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to refresh metadata",
    );
  });

  describe("description", () => {
    const LONG = "Line one\nLine two\nLine three\nLine four";
    const LINE_PX = 15;
    const observers = new Set<() => void>();

    // jsdom lays nothing out: a box is one line per newline, and the
    // clamped box shows three of them.
    function contentHeight(el: HTMLElement) {
      return (el.textContent ?? "").split("\n").length * LINE_PX;
    }

    beforeEach(() => {
      vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(
        function (this: HTMLElement) {
          return contentHeight(this);
        },
      );
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(
        function (this: HTMLElement) {
          const full = contentHeight(this);
          return this.classList.contains("line-clamp-3")
            ? Math.min(full, 3 * LINE_PX)
            : full;
        },
      );
      vi.stubGlobal(
        "ResizeObserver",
        class {
          constructor(private readonly callback: () => void) {}
          observe() {
            observers.add(this.callback);
          }
          disconnect() {
            observers.delete(this.callback);
          }
        },
      );
    });

    afterEach(() => {
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
      observers.clear();
    });

    function resize() {
      act(() => observers.forEach((measure) => measure()));
    }

    it("leaves a description that fits as plain selectable text", async () => {
      getLoftMetadata.mockResolvedValue(
        makeMetadata({ description: "Line one\nLine two\nLine three" }),
      );
      renderPanel();

      const text = await screen.findByText(/Line one/);
      expect(text).toHaveClass("line-clamp-3");
      expect(text).not.toHaveClass("select-none");
      expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
    });

    it("keeps clipped lines unselectable until the viewer expands them", async () => {
      getLoftMetadata.mockResolvedValue(makeMetadata({ description: LONG }));
      renderPanel();

      const text = await screen.findByText(/Line one/);
      const more = await screen.findByRole("button", { name: "Show more" });
      expect(more).toHaveAttribute("aria-expanded", "false");
      expect(text).toHaveClass("line-clamp-3", "select-none");

      fireEvent.click(more);

      const less = screen.getByRole("button", { name: "Show less" });
      expect(less).toHaveAttribute("aria-expanded", "true");
      expect(text).not.toHaveClass("line-clamp-3");
      expect(text).not.toHaveClass("select-none");
    });

    it("expands from a tap on the clipped text, and collapses only from its button", async () => {
      getLoftMetadata.mockResolvedValue(makeMetadata({ description: LONG }));
      renderPanel();

      const text = await screen.findByText(/Line one/);
      fireEvent.click(text);
      expect(text).not.toHaveClass("line-clamp-3");

      fireEvent.click(text);
      resize();
      expect(text).not.toHaveClass("line-clamp-3");

      fireEvent.click(screen.getByRole("button", { name: "Show less" }));
      expect(text).toHaveClass("line-clamp-3", "select-none");
      expect(
        screen.getByRole("button", { name: "Show more" }),
      ).toHaveAttribute("aria-expanded", "false");
    });

    it("drops the toggle when a resize lets the text fit", async () => {
      getLoftMetadata.mockResolvedValue(makeMetadata({ description: LONG }));
      renderPanel();
      const text = await screen.findByText(/Line one/);
      await screen.findByRole("button", { name: "Show more" });

      vi.spyOn(text, "scrollHeight", "get").mockReturnValue(3 * LINE_PX);
      resize();

      expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
      expect(text).not.toHaveClass("select-none");
    });

    it("drops the toggle when a refresh brings a description that fits", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      getLoftMetadata.mockResolvedValue(makeMetadata({ description: LONG }));
      renderPanel();
      await screen.findByRole("button", { name: "Show more" });

      getLoftMetadata.mockResolvedValue(makeMetadata({ description: "Short" }));
      act(() => notifyLoftRefreshed("f1"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });

      const text = await screen.findByText("Short");
      expect(text).not.toHaveClass("select-none");
      expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
    });

    it("starts collapsed on another file", async () => {
      getLoftMetadata.mockResolvedValue(makeMetadata({ description: LONG }));
      const { rerender } = renderPanel("f1");
      fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
      expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();

      rerender(panel("f2"));

      await waitFor(() => expect(getLoftMetadata).toHaveBeenCalledWith("f2", "d"));
      expect(
        await screen.findByRole("button", { name: "Show more" }),
      ).toHaveAttribute("aria-expanded", "false");
      expect(screen.getByText(/Line one/)).toHaveClass("line-clamp-3", "select-none");
    });
  });
});
