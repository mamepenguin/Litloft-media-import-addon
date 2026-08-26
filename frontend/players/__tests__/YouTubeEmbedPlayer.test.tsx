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
  onError: (e: { data: number }) => void;
};

/** Captured from the most recent `new YT.Player(...)` call. */
let lastOptions: {
  playerVars: Record<string, number>;
  events: PlayerEvents;
} | null = null;
/** The node handed to that call, which the real API would replace. */
let lastMount: HTMLElement | null = null;

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
        constructor(mount: HTMLElement, options: never) {
          lastMount = mount;
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

async function mountPlayer(
  durationHint: number | null = 600,
  onEnded?: () => void,
) {
  const utils = render(
    <YouTubeEmbed
      fileId="abc123456789"
      url={URL_UNDER_TEST}
      durationHint={durationHint}
      onEnded={onEnded}
    />,
  );
  await waitFor(() => expect(lastOptions).not.toBeNull());
  await act(async () => {
    await lastOptions!.events.onReady({ target: player });
  });
  return utils;
}

/**
 * The gesture surface belongs to core's MediaControls now; this file
 * only cares that the ad / end-screen gate reaches it. Addressed by its
 * marker attribute rather than its classes so a styling change in core
 * does not quietly turn these assertions into no-ops.
 */
function overlayOf(container: HTMLElement): HTMLElement {
  const overlay = container.querySelector<HTMLElement>("[data-player-gestures]");
  if (!overlay) throw new Error("gesture overlay not found");
  return overlay;
}

/**
 * jsdom implements no PointerEvent, and testing-library's
 * `fireEvent.pointerDown` silently drops every coordinate when it falls
 * back to a plain Event. Building it by hand is the only way to deliver
 * clientX.
 */
function pointerEvent(type: string, clientX: number): Event {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(event, {
    clientX,
    clientY: 50,
    pointerId: 1,
    pointerType: "touch",
  });
  return event;
}

beforeEach(() => {
  lastOptions = null;
  window.localStorage.clear();
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

// The gesture state machine itself is core's, and is covered in
// usePlayerGestures.test.tsx. What is left to prove here is that this
// player wires it up: the controller, the ad gate and the fullscreen
// controller all have to arrive for any of it to work.
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

  it("goes fullscreen on double click without pausing on the way", async () => {
    // Two clicks arrive before dblclick; acting on both would pause
    // and resume, which shows as a hitch in the YouTube player.
    vi.useFakeTimers();
    const { container } = await mountPlayer();
    const overlay = overlayOf(container);
    const requestFullscreen = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(
      container.querySelector<HTMLElement>('[data-testid="player-frame"]')!,
      "requestFullscreen",
      {
        configurable: true,
        value: requestFullscreen,
      },
    );
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

  it("boosts the speed while a finger is held on the video", async () => {
    // End-to-end through this player's wiring: the controller, the ad
    // gate and the pointer mode all have to arrive for the boost to
    // reach the YouTube player at all.
    vi.useFakeTimers();
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("pointer: coarse"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { container } = await mountPlayer();
    await act(async () => {
      overlayOf(container).dispatchEvent(pointerEvent("pointerdown", 200));
      vi.advanceTimersByTime(500);
    });
    expect(player.setPlaybackRate).toHaveBeenCalledWith(2);

    await act(async () => {
      window.dispatchEvent(pointerEvent("pointerup", 200));
    });
    expect(player.setPlaybackRate).toHaveBeenLastCalledWith(1);
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
    const frame = container.querySelector<HTMLElement>('[data-testid="player-frame"]');
    if (!frame) throw new Error("player frame not found");
    return frame;
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
    expect(saveProgress).toHaveBeenCalledWith("abc123456789", 120, 600);
  });
});

// Spec 2026-08-10-media-import-watch-surface.md §4.2 / §4.3.
describe("YouTubeEmbed playback completion", () => {
  it("records the final position instead of erasing the record", async () => {
    const { saveProgress } = await import("@/lib/recentlyPlayed");
    const { deleteWatchProgress } = await import("@/lib/api");
    await mountPlayer(600);
    player.getCurrentTime.mockReturnValue(600);
    await act(async () => {
      lastOptions!.events.onStateChange({ data: YT_STATE_ENDED });
    });
    expect(saveProgress).toHaveBeenCalledWith("abc123456789", 600, 600);
    expect(deleteWatchProgress).not.toHaveBeenCalled();
  });

  it("forwards completion to the host", async () => {
    const onEnded = vi.fn();
    await mountPlayer(600, onEnded);
    await act(async () => {
      lastOptions!.events.onStateChange({ data: YT_STATE_ENDED });
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  // ENDED fires at the end of a pre-roll too, and nothing in the API
  // distinguishes the two — only the duration mismatch does. Without
  // this guard an ad would be written down as a finished video.
  it("ignores the ENDED that closes an ad", async () => {
    const { saveProgress } = await import("@/lib/recentlyPlayed");
    const onEnded = vi.fn();
    await mountPlayer(600, onEnded);
    player.getDuration.mockReturnValue(15);
    player.getCurrentTime.mockReturnValue(15);
    await act(async () => {
      lastOptions!.events.onStateChange({ data: YT_STATE_ENDED });
    });
    expect(saveProgress).not.toHaveBeenCalled();
    expect(onEnded).not.toHaveBeenCalled();
  });
});

// Spec 2026-08-11-playback-clock-foundation.md §4.3. Native media had
// Media Session and no custom control bar; .loft had the bar and no
// Media Session. Core supplies the metadata because core owns it.
describe("YouTubeEmbed media session", () => {
  class FakeMetadata {
    title: string;
    artist: string;
    artwork: MediaImage[];
    constructor(init: MediaMetadataInit) {
      this.title = init.title ?? "";
      this.artist = init.artist ?? "";
      this.artwork = (init.artwork ?? []) as MediaImage[];
    }
  }

  type Handler = ((details: MediaSessionActionDetails) => void) | null;

  const originalSession = navigator.mediaSession;
  const originalMetadata = (window as unknown as { MediaMetadata?: unknown })
    .MediaMetadata;

  let handlers: Record<string, Handler>;

  beforeEach(() => {
    handlers = {};
    Object.defineProperty(navigator, "mediaSession", {
      configurable: true,
      value: {
        setActionHandler: vi.fn((action: string, h: Handler) => {
          handlers[action] = h;
        }),
        setPositionState: vi.fn(),
        metadata: null as FakeMetadata | null,
        playbackState: "none",
      },
    });
    (window as unknown as { MediaMetadata: unknown }).MediaMetadata =
      FakeMetadata;
  });

  afterEach(() => {
    Object.defineProperty(navigator, "mediaSession", {
      configurable: true,
      value: originalSession,
    });
    (window as unknown as { MediaMetadata?: unknown }).MediaMetadata =
      originalMetadata;
  });

  function session() {
    return navigator.mediaSession as unknown as {
      metadata: FakeMetadata | null;
      setPositionState: ReturnType<typeof vi.fn>;
    };
  }

  async function mountWithMetadata() {
    const utils = render(
      <YouTubeEmbed
        fileId="abc123456789"
        url={URL_UNDER_TEST}
        durationHint={600}
        mediaSessionMetadata={{
          title: "Some talk",
          artist: "Some channel",
          artwork: [{ src: "/thumb.jpg" }],
        }}
      />,
    );
    await waitFor(() => expect(lastOptions).not.toBeNull());
    await act(async () => {
      await lastOptions!.events.onReady({ target: player });
    });
    return utils;
  }

  it("publishes what core handed it to the OS", async () => {
    await mountWithMetadata();
    expect(session().metadata?.title).toBe("Some talk");
    expect(session().metadata?.artist).toBe("Some channel");
    expect(session().metadata?.artwork[0]?.src).toBe("/thumb.jpg");
  });

  it("drives the lock-screen scrubber", async () => {
    player.getCurrentTime.mockReturnValue(42);
    await mountWithMetadata();
    expect(session().setPositionState).toHaveBeenCalledWith(
      expect.objectContaining({ duration: 600, position: 42 }),
    );
  });

  it("routes the OS transport controls through the player", async () => {
    await mountWithMetadata();
    handlers.play?.({} as MediaSessionActionDetails);
    expect(player.playVideo).toHaveBeenCalled();
    handlers.pause?.({} as MediaSessionActionDetails);
    expect(player.pauseVideo).toHaveBeenCalled();
  });

  it("stays out of the way when core supplies nothing", async () => {
    // A provider whose file has no metadata to publish must not blank
    // out whatever the platform already had.
    await mountPlayer(600);
    expect(session().metadata).toBeNull();
  });
});

describe("YouTubeEmbed player UI choice", () => {
  const STORAGE_KEY = "media-import-youtube-ui";

  it("draws Litloft's controls by default", async () => {
    const { container } = await mountPlayer();
    expect(lastOptions!.playerVars.controls).toBe(0);
    expect(overlayOf(container)).toBeInTheDocument();
  });

  it("hands the player its own UI when asked", async () => {
    // playsinline goes with it: that hand-off to the browser's own
    // full-screen player is the only route to Picture-in-Picture.
    window.localStorage.setItem(STORAGE_KEY, "true");
    await mountPlayer();
    expect(lastOptions!.playerVars.controls).toBe(1);
    expect(lastOptions!.playerVars.playsinline).toBeUndefined();
  });

  it("stands our controls down in that mode", async () => {
    // Ours would sit on top of the player's — and the gesture overlay
    // would swallow every touch meant for them.
    window.localStorage.setItem(STORAGE_KEY, "true");
    const { container } = await mountPlayer();
    expect(container.querySelector("[data-player-gestures]")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Seek")).not.toBeInTheDocument();
  });

  it("offers a way back that does not depend on our controls", async () => {
    window.localStorage.setItem(STORAGE_KEY, "true");
    await mountPlayer();
    expect(
      screen.getByRole("button", { name: "Back to the Litloft player" }),
    ).toBeInTheDocument();
  });

  it("gives the keyboard back to the player in that mode", async () => {
    window.localStorage.setItem(STORAGE_KEY, "true");
    await mountPlayer();
    expect(lastOptions!.playerVars.disablekb).toBe(0);
  });

  it("builds a fresh mount node on every switch", async () => {
    // The API replaces the node it is given with an iframe, so the
    // previous one is detached by the time a rebuild happens. Handing
    // it back would fail, and leaving React to manage it broke the
    // page outright.
    const mounts: HTMLElement[] = [];
    lastMount = null;
    window.localStorage.setItem(STORAGE_KEY, "true");
    await mountPlayer();
    mounts.push(lastMount!);

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Back to the Litloft player" }),
      );
    });
    await waitFor(() => expect(lastOptions!.playerVars.controls).toBe(0));
    mounts.push(lastMount!);

    expect(mounts[0]).not.toBe(mounts[1]);
    expect(mounts[1].isConnected).toBe(true);
  });

  it("never hands our controls a player from the other mode", async () => {
    // Regression: state updates land after the render that mounts the
    // control bar, so for one commit it was given the player being
    // destroyed in that same commit. Its mount effect then applied the
    // saved playback rate to a destroyed widget, which threw from
    // inside YouTube's own code and took the page down.
    window.localStorage.setItem(STORAGE_KEY, "true");
    await mountPlayer();
    player.setPlaybackRate.mockClear();

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Back to the Litloft player" }),
      );
    });
    await waitFor(() => expect(lastOptions!.playerVars.controls).toBe(0));

    // The new player has not reported ready yet, so there is no
    // controller for this mode and the bar must stay unmounted.
    expect(screen.queryByLabelText("Seek")).not.toBeInTheDocument();
    expect(player.setPlaybackRate).not.toHaveBeenCalled();

    await act(async () => {
      await lastOptions!.events.onReady({ target: player });
    });
    expect(screen.getByLabelText("Seek")).toBeInTheDocument();
  });

  it("carries the playhead across the switch", async () => {
    // The periodic save only writes every five seconds, so it cannot
    // be relied on to hold the exact position at the moment of a
    // switch.
    window.localStorage.setItem(STORAGE_KEY, "true");
    const { container } = await mountPlayer();
    player.getCurrentTime.mockReturnValue(123);

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Back to the Litloft player" }),
      );
    });
    await waitFor(() => expect(lastOptions!.playerVars.controls).toBe(0));
    await act(async () => {
      await lastOptions!.events.onReady({ target: player });
    });

    expect(player.seekTo).toHaveBeenCalledWith(123, true);
    expect(container.querySelector("[data-player-gestures]")).toBeInTheDocument();
  });
});

// The owner-disabled-embedding error (101/150) is rejected at the
// iframe level and is unaffected by playerVars.controls, so neither
// control skin can recover it — only linking out to youtube.com works.
describe("YouTubeEmbed embedding-restricted fallback", () => {
  it("replaces the player with a link to youtube.com", async () => {
    const { container } = await mountPlayer();
    await act(async () => {
      lastOptions!.events.onError({ data: 101 });
    });

    expect(player.destroy).toHaveBeenCalled();
    expect(container.querySelector("[data-player-gestures]")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Seek")).not.toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Watch on YouTube" });
    expect(link).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("also falls back on the legacy duplicate error code", async () => {
    await mountPlayer();
    await act(async () => {
      lastOptions!.events.onError({ data: 150 });
    });
    expect(
      screen.getByRole("link", { name: "Watch on YouTube" }),
    ).toBeInTheDocument();
  });

  it("ignores error codes unrelated to embedding restrictions", async () => {
    const { container } = await mountPlayer();
    await act(async () => {
      // 2 = invalid parameter value; not an embedding restriction.
      lastOptions!.events.onError({ data: 2 });
    });
    expect(player.destroy).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("link", { name: "Watch on YouTube" }),
    ).not.toBeInTheDocument();
    expect(container.querySelector("[data-player-gestures]")).toBeInTheDocument();
  });

  it("drops the manual player-UI toggle once restricted", async () => {
    await mountPlayer();
    await act(async () => {
      lastOptions!.events.onError({ data: 101 });
    });
    expect(
      screen.queryByRole("button", { name: "Back to the Litloft player" }),
    ).not.toBeInTheDocument();
  });
});
