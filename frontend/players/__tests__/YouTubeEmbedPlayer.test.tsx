import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
// `screen` must be imported: jsdom exposes a global `window.screen`
// that silently shadows it and has none of the query helpers.
import { render, act, waitFor, fireEvent, screen } from "@testing-library/react";
import YouTubeEmbed from "../YouTubeEmbed";

const YT_STATE_ENDED = 0;
const YT_STATE_PLAYING = 1;

type PlayerEvents = {
  onReady: (e: { target: unknown }) => void | Promise<void>;
  onStateChange: (e: { data: number }) => void;
};

/** Captured from the most recent `new YT.Player(...)` call. */
let lastOptions: {
  playerVars: Record<string, number>;
  events: PlayerEvents;
} | null = null;

const player = {
  seekTo: vi.fn(),
  playVideo: vi.fn(),
  pauseVideo: vi.fn(),
  mute: vi.fn(),
  unMute: vi.fn(),
  isMuted: vi.fn().mockReturnValue(false),
  getCurrentTime: vi.fn().mockReturnValue(5),
  getDuration: vi.fn().mockReturnValue(600),
  getPlayerState: vi.fn().mockReturnValue(YT_STATE_PLAYING),
  getVolume: vi.fn().mockReturnValue(100),
  setVolume: vi.fn(),
  getPlaybackRate: vi.fn().mockReturnValue(1),
  setPlaybackRate: vi.fn(),
  getVideoLoadedFraction: vi.fn().mockReturnValue(0.3),
  destroy: vi.fn(),
};

vi.mock("../loadYouTubeIframeApi", () => ({
  loadYouTubeIframeApi: () =>
    Promise.resolve({
      Player: class {
        constructor(_mount: HTMLElement, options: never) {
          lastOptions = options;
          return player as never;
        }
      },
    }),
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

interface Shortcut {
  key: string;
  handler: () => void;
}

/** Captured so tests can fire the same handler the keyboard would. */
let shortcuts: Shortcut[] = [];

vi.mock("@/hooks/useShortcuts", () => ({
  useShortcuts: (_id: string, _label: string, entries: Shortcut[]) => {
    shortcuts = entries;
  },
}));

function pressShortcut(key: string) {
  const entry = shortcuts.find((s) => s.key === key);
  if (!entry) throw new Error(`shortcut not registered: ${key}`);
  entry.handler();
}

const URL_UNDER_TEST = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

async function mountPlayer(durationHint: number | null = 600) {
  const utils = render(
    <YouTubeEmbed fileId="abc123456789" url={URL_UNDER_TEST} durationHint={durationHint} />,
  );
  await waitFor(() => expect(lastOptions).not.toBeNull());
  await act(async () => {
    await lastOptions!.events.onReady({ target: player });
  });
  return utils;
}

function overlayOf(container: HTMLElement): HTMLElement {
  const overlay = container.querySelector<HTMLElement>(".absolute.inset-0.z-0");
  if (!overlay) throw new Error("overlay not found");
  return overlay;
}

beforeEach(() => {
  lastOptions = null;
  vi.clearAllMocks();
  player.getDuration.mockReturnValue(600);
  player.getPlayerState.mockReturnValue(YT_STATE_PLAYING);
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("pointer: fine"),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("YouTubeEmbed player configuration", () => {
  it("turns off the YouTube chrome and forces inline playback", async () => {
    await mountPlayer();
    expect(lastOptions!.playerVars.controls).toBe(0);
    // Without playsinline iOS takes over with its native fullscreen
    // player, which our control bar cannot reach.
    expect(lastOptions!.playerVars.playsinline).toBe(1);
  });

  it("keeps the existing keyboard and related-video settings", async () => {
    await mountPlayer();
    expect(lastOptions!.playerVars.disablekb).toBe(1);
    expect(lastOptions!.playerVars.rel).toBe(0);
  });

  it("renders the Litloft control bar inside the player frame", async () => {
    const { container } = await mountPlayer();
    // Must be inside the element we call requestFullscreen() on, or
    // the bar disappears in fullscreen.
    const frame = container.firstElementChild!;
    await waitFor(() =>
      expect(frame.querySelector("[aria-label='Seek']")).toBeInTheDocument(),
    );
  });
});

describe("YouTubeEmbed click overlay", () => {
  it("captures clicks during normal playback", async () => {
    const { container } = await mountPlayer();
    expect(overlayOf(container).style.pointerEvents).toBe("auto");
  });

  it("stands down while an ad is playing", async () => {
    // Covering the ad would block YouTube's skip button and count as
    // interfering with ads.
    vi.useFakeTimers();
    const { container } = await mountPlayer(600);
    player.getDuration.mockReturnValue(15);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(overlayOf(container).style.pointerEvents).toBe("none");
  });

  it("stands down on the end screen so related videos stay clickable", async () => {
    const { container } = await mountPlayer();
    await act(async () => {
      lastOptions!.events.onStateChange({ data: YT_STATE_ENDED });
    });
    expect(overlayOf(container).style.pointerEvents).toBe("none");
  });

  it("comes back once the ad finishes", async () => {
    vi.useFakeTimers();
    const { container } = await mountPlayer(600);
    player.getDuration.mockReturnValue(15);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    player.getDuration.mockReturnValue(600);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(overlayOf(container).style.pointerEvents).toBe("auto");
  });

  it("stays active when we have no duration to detect ads with", async () => {
    vi.useFakeTimers();
    const { container } = await mountPlayer(null);
    player.getDuration.mockReturnValue(15);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(overlayOf(container).style.pointerEvents).toBe("auto");
  });
});

describe("YouTubeEmbed pointer interaction", () => {
  it("toggles playback on a single click with a fine pointer", async () => {
    vi.useFakeTimers();
    const { container } = await mountPlayer();
    await act(async () => {
      // detail must be set: a click with detail 0 is a programmatic
      // one, which the overlay deliberately ignores.
      fireEvent.click(overlayOf(container), { detail: 1 });
      vi.advanceTimersByTime(300);
    });
    expect(player.pauseVideo).toHaveBeenCalledTimes(1);
  });

  it("ignores programmatic clicks", () => {
    return (async () => {
      vi.useFakeTimers();
      const { container } = await mountPlayer();
      await act(async () => {
        overlayOf(container).click();
        vi.advanceTimersByTime(300);
      });
      expect(player.pauseVideo).not.toHaveBeenCalled();
    })();
  });

  it("goes fullscreen on double click without pausing on the way", async () => {
    // Two clicks arrive before dblclick; acting on both would pause
    // and resume, which shows as a hitch in the YouTube player.
    vi.useFakeTimers();
    const { container } = await mountPlayer();
    const overlay = overlayOf(container);
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(container.firstElementChild!, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    await act(async () => {
      fireEvent.click(overlay, { detail: 1 });
      fireEvent.click(overlay, { detail: 2 });
      fireEvent.dblClick(overlay, { detail: 2 });
      vi.advanceTimersByTime(300);
    });
    expect(player.pauseVideo).not.toHaveBeenCalled();
    expect(player.playVideo).not.toHaveBeenCalled();
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
  });

  it("does not toggle playback on touch, where a tap surfaces the controls", async () => {
    vi.useFakeTimers();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { container } = await mountPlayer();
    await act(async () => {
      fireEvent.click(overlayOf(container), { detail: 1 });
      vi.advanceTimersByTime(300);
    });
    expect(player.pauseVideo).not.toHaveBeenCalled();
  });
});

describe("YouTubeEmbed fullscreen", () => {
  function makeCoarseTouchDevice() {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("pointer: coarse"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  }

  function frameOf(container: HTMLElement): HTMLElement {
    return container.firstElementChild as HTMLElement;
  }

  it("keeps the in-page aspect shim while not fullscreen", async () => {
    const { container } = await mountPlayer();
    const frame = frameOf(container);
    expect(frame.style.paddingTop).toBe("56.25%");
    expect(frame.className).toContain("relative");
  });

  it("pins the frame over the viewport when it has to fake fullscreen", async () => {
    // No requestFullscreen on the frame: an iPhone, in other words.
    makeCoarseTouchDevice();
    const { container } = await mountPlayer();
    await act(async () => {
      pressShortcut("f");
    });
    const frame = frameOf(container);
    expect(frame.className).toContain("fixed");
    expect(frame.className).toContain("inset-0");
    // Filling the viewport is the whole point; the aspect shim would
    // fight it.
    expect(frame.style.paddingTop).toBe("");
  });

  it("shares one fullscreen state across the keyboard and the bar", async () => {
    makeCoarseTouchDevice();
    const { container } = await mountPlayer();
    await act(async () => {
      pressShortcut("f");
    });
    // The bar's button must know the keyboard already opened it,
    // otherwise the two routes fight over the state.
    expect(
      await screen.findByRole("button", { name: "Exit full screen" }),
    ).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
    });
    expect(frameOf(container).className).toContain("relative");
  });

  it("uses the real API where the platform has one", async () => {
    makeCoarseTouchDevice();
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    const { container } = await mountPlayer();
    Object.defineProperty(frameOf(container), "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    await act(async () => {
      pressShortcut("f");
    });
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(frameOf(container).className).not.toContain("fixed");
  });

  it("holds the iframe clear of the notch while faking fullscreen", async () => {
    // WebKit propagates safe-area insets into an iframe that overlaps
    // the Dynamic Island, and the YouTube player shifts the video away
    // from that edge in response — off-centre, and unreachable by any
    // CSS of ours. Keeping the iframe inside the safe area tells it the
    // insets are zero.
    makeCoarseTouchDevice();
    const { container } = await mountPlayer();
    await act(async () => {
      pressShortcut("f");
    });
    const host = frameOf(container).querySelector<HTMLElement>(".absolute.inset-0")!;
    expect(host.style.paddingLeft).toContain("safe-area-inset");
    // Both sides take the larger inset so the box stays centred on the
    // screen rather than merely clear of the island.
    expect(host.style.paddingLeft).toBe(host.style.paddingRight);
  });

  it("leaves the iframe alone in the page", async () => {
    const { container } = await mountPlayer();
    const host = frameOf(container).querySelector<HTMLElement>(".absolute.inset-0")!;
    expect(host.style.paddingLeft).toBe("");
  });

  it("does not fake fullscreen on a desktop pointer", async () => {
    const { container } = await mountPlayer();
    await act(async () => {
      pressShortcut("f");
    });
    expect(frameOf(container).className).toContain("relative");
  });
});

describe("YouTubeEmbed watch progress", () => {
  it("does not stamp the ad's clock onto the resume point", async () => {
    vi.useFakeTimers();
    const { saveProgress } = await import("@/lib/recentlyPlayed");
    await mountPlayer(600);
    player.getDuration.mockReturnValue(15);
    player.getCurrentTime.mockReturnValue(8);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(saveProgress).not.toHaveBeenCalled();
  });

  it("resumes saving once the real video is back", async () => {
    vi.useFakeTimers();
    const { saveProgress } = await import("@/lib/recentlyPlayed");
    await mountPlayer(600);
    player.getCurrentTime.mockReturnValue(120);
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(saveProgress).toHaveBeenCalledWith("abc123456789", 120);
  });
});
