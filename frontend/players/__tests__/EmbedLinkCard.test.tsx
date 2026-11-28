import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import VimeoEmbed from "../VimeoEmbed";
import YouTubeEmbed from "../YouTubeEmbed";

const loader = vi.hoisted(() => ({
  load: (() => Promise.reject(new Error("unset"))) as () => Promise<unknown>,
  constructed: [] as unknown[],
}));

vi.mock("../loadYouTubeIframeApi", () => ({
  loadYouTubeIframeApi: () => loader.load(),
}));

vi.mock("@/lib/api", () => ({
  getWatchProgress: vi.fn().mockResolvedValue({ position: 0 }),
  saveWatchProgress: vi.fn().mockResolvedValue(undefined),
  deleteWatchProgress: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/recentlyPlayed", () => ({
  getSavedProgress: () => 0,
  saveProgress: vi.fn(),
  clearProgress: vi.fn(),
}));

vi.mock("@/components/ProfileProvider", () => ({
  useProfile: () => ({ nickname: null }),
}));

vi.mock("@/hooks/useShortcuts", () => ({
  useShortcuts: () => {},
}));

const player = {
  seekTo: vi.fn(),
  playVideo: vi.fn(),
  pauseVideo: vi.fn(),
  mute: vi.fn(),
  unMute: vi.fn(),
  isMuted: vi.fn().mockReturnValue(false),
  getCurrentTime: vi.fn().mockReturnValue(0),
  getDuration: vi.fn().mockReturnValue(600),
  getPlayerState: vi.fn().mockReturnValue(-1),
  getVolume: vi.fn().mockReturnValue(100),
  setVolume: vi.fn(),
  getPlaybackRate: vi.fn().mockReturnValue(1),
  setPlaybackRate: vi.fn(),
  getVideoLoadedFraction: vi.fn().mockReturnValue(0),
  destroy: vi.fn(),
};

const workingApi = {
  Player: class {
    constructor(_mount: HTMLElement, options: unknown) {
      loader.constructed.push(options);
      return player as never;
    }
  },
};

function rejectingLoad() {
  return Promise.reject(new Error("script failed"));
}

function linkCardFor(url: string): HTMLElement {
  const link = screen.getByRole("link", { name: url });
  expect(link).toHaveAttribute("href", url);
  return link;
}

beforeEach(() => {
  loader.constructed.length = 0;
  loader.load = rejectingLoad;
  window.localStorage.clear();
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

afterEach(() => {
  cleanup();
});

describe("SPEC-ADDON-191 a Vimeo URL with no video number", () => {
  it.each(["https://vimeo.com/user123", "https://vimeo.com/showcase/1234567", "https://example.com/not-vimeo"])(
    "SPEC-ADDON-191 I6: %s shows the link card instead of an empty box",
    (url) => {
      const { container } = render(<VimeoEmbed fileId="f1" url={url} />);

      linkCardFor(url);
      expect(container.querySelector("iframe")).toBeNull();

      const numbered = render(<VimeoEmbed fileId="f2" url="https://vimeo.com/123456" />);
      expect(numbered.container.querySelector("iframe")?.getAttribute("src")).toBe(
        "https://player.vimeo.com/video/123456",
      );
      expect(screen.queryByRole("link", { name: "https://vimeo.com/123456" })).not.toBeInTheDocument();
    },
  );
});

describe("SPEC-ADDON-191 a YouTube URL with no video id", () => {
  it.each(["https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", "https://www.youtube.com/watch"])(
    "SPEC-ADDON-191 I6: %s shows the link card",
    async (url) => {
      loader.load = () => Promise.resolve(workingApi);
      const { container } = render(<YouTubeEmbed fileId="f1" url={url} />);

      await waitFor(() => linkCardFor(url));
      expect(container.querySelector("[data-player-gestures]")).toBeNull();
      expect(loader.constructed).toHaveLength(0);
    },
  );
});

describe("SPEC-ADDON-191 a YouTube IFrame API that failed to load", () => {
  const URL_A = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";
  const URL_B = "https://www.youtube.com/watch?v=M7lc1UVf-VE";

  it("SPEC-ADDON-191 I6: shows the link card, and again on a later mount", async () => {
    const first = render(<YouTubeEmbed fileId="f1" url={URL_A} />);
    await waitFor(() => linkCardFor(URL_A));
    expect(first.container.querySelector("[data-player-gestures]")).toBeNull();
    first.unmount();

    const second = render(<YouTubeEmbed fileId="f1" url={URL_A} />);
    await waitFor(() => linkCardFor(URL_A));
    expect(second.container.querySelector("[data-player-gestures]")).toBeNull();
  });

  it("SPEC-ADDON-191 I6: shows the link card for a viewer whose stored skin is YouTube's", async () => {
    window.localStorage.setItem("media-import-youtube-ui", "true");
    const { container } = render(<YouTubeEmbed fileId="f1" url={URL_A} />);

    await waitFor(() => linkCardFor(URL_A));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    linkCardFor(URL_A);
    expect(container.querySelector("iframe")).toBeNull();
    expect(loader.constructed).toHaveLength(0);
  });

  it("SPEC-ADDON-191 §3 I7: a new URL starts loading again, plays, and a restricted video keeps its own view", async () => {
    const { container, rerender } = render(<YouTubeEmbed fileId="f1" url={URL_A} />);
    await waitFor(() => linkCardFor(URL_A));

    loader.load = () => Promise.resolve(workingApi);
    rerender(<YouTubeEmbed fileId="f1" url={URL_B} />);

    await waitFor(() => expect(loader.constructed).toHaveLength(1));
    const options = loader.constructed[0] as {
      events: {
        onReady: (e: { target: unknown }) => void | Promise<void>;
        onError: (e: { data: number }) => void;
      };
    };
    await act(async () => {
      await options.events.onReady({ target: player });
    });
    expect(screen.queryByRole("link", { name: URL_A })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: URL_B })).not.toBeInTheDocument();
    expect(container.querySelector("[data-player-gestures]")).not.toBeNull();

    await act(async () => {
      options.events.onError({ data: 101 });
    });
    expect(screen.getByRole("link", { name: "Watch on YouTube" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: URL_B })).not.toBeInTheDocument();
  });
});
