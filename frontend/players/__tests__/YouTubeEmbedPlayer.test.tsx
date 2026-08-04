import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, act, waitFor } from "@testing-library/react";
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

vi.mock("@/hooks/useShortcuts", () => ({
  useShortcuts: () => {},
}));

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
