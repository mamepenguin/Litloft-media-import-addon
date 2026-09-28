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

function renderPanel() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ToastProvider>
        <OfferReader />
        <LoftMetadataPanel fileId="f1" drive="d" />
      </ToastProvider>
    </NextIntlClientProvider>,
  );
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
      expect(await screen.findByText(toast)).toBeInTheDocument();
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
    expect(
      await screen.findByText("Failed to queue speech-to-text"),
    ).toBeInTheDocument();
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
  });
});
