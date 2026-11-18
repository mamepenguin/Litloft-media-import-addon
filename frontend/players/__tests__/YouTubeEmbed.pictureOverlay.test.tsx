import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// `screen` must be imported: jsdom's global `window.screen` would shadow it.
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import YouTubeEmbed from "../YouTubeEmbed";

const UI_KEY = "media-import-youtube-ui";
const POSTER = "/fixtures/poster-under-test.png";
const URL_UNDER_TEST = "https://www.youtube.com/watch?v=dQw4w9WgXcQ";

let lastOptions: { events: { onReady: (e: { target: unknown }) => void | Promise<void> } } | null =
  null;
const mounts: HTMLElement[] = [];

const player = {
  seekTo: vi.fn(),
  playVideo: vi.fn(),
  pauseVideo: vi.fn(),
  mute: vi.fn(),
  unMute: vi.fn(),
  isMuted: vi.fn().mockReturnValue(false),
  getCurrentTime: vi.fn().mockReturnValue(5),
  getDuration: vi.fn().mockReturnValue(600),
  getPlayerState: vi.fn().mockReturnValue(2),
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
          mounts.push(mount);
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

beforeEach(() => {
  lastOptions = null;
  mounts.length = 0;
  window.localStorage.clear();
  vi.clearAllMocks();
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
  cleanup();
  window.localStorage.clear();
});

function Overlay({ label }: { label: string }) {
  return <div data-testid="picture-overlay">{label}</div>;
}

function embed(label: string) {
  return (
    <YouTubeEmbed
      fileId="abc123456789"
      url={URL_UNDER_TEST}
      durationHint={600}
      posterUrl={POSTER}
      pictureOverlay={<Overlay label={label} />}
    />
  );
}

async function mount(label: string) {
  const utils = render(embed(label));
  await waitFor(() => expect(lastOptions).not.toBeNull());
  await act(async () => {
    await lastOptions!.events.onReady({ target: player });
  });
  return utils;
}

const follows = (a: Node, b: Node) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

function frameParts(container: HTMLElement) {
  const frame = container.querySelector<HTMLElement>('[data-testid="player-frame"]');
  if (!frame) throw new Error("player frame not found");
  const host = frame.querySelector<HTMLElement>(":scope > div:not([data-player-gestures])");
  if (!host) throw new Error("iframe host not found");
  return { frame, host };
}

describe("SPEC-ADDON-137 I9 the YouTube embed draws pictureOverlay in its full-screen frame", () => {
  it("in the Litloft-UI mode: over the iframe, under the poster, the gesture overlay and the controls", async () => {
    const { container } = await mount("first");

    const overlay = screen.getByTestId("picture-overlay");
    const { frame, host } = frameParts(container);
    expect(frame.contains(overlay)).toBe(true);
    expect(host.contains(overlay)).toBe(false);
    expect(follows(host, overlay)).toBe(true);

    const gestures = frame.querySelector("[data-player-gestures]")!;
    const poster = screen.getByTestId("player-poster");
    const seek = await screen.findByLabelText("Seek");
    expect(follows(overlay, gestures)).toBe(true);
    expect(follows(overlay, poster)).toBe(true);
    expect(follows(overlay, seek)).toBe(true);
  });

  it("in the YouTube-UI mode: over the iframe, and so over YouTube's own controls", async () => {
    window.localStorage.setItem(UI_KEY, "true");
    const { container } = await mount("first");

    const overlay = await screen.findByTestId("picture-overlay");
    const { frame, host } = frameParts(container);
    expect(frame.contains(overlay)).toBe(true);
    expect(host.contains(overlay)).toBe(false);
    expect(follows(host, overlay)).toBe(true);
  });

  it.each([
    ["the Litloft-UI mode", "false"],
    ["the YouTube-UI mode", "true"],
  ])("keeps the player and its mount node when the node changes, in %s", async (_mode, stored) => {
    window.localStorage.setItem(UI_KEY, stored);
    const { rerender } = await mount("first");
    await screen.findByTestId("picture-overlay");
    await waitFor(() => expect(mounts.length).toBeGreaterThan(0));
    const built = mounts.length;
    const mountNode = mounts.at(-1)!;

    rerender(embed("second"));

    await waitFor(() =>
      expect(screen.getByTestId("picture-overlay")).toHaveTextContent("second"),
    );
    expect(mounts.length).toBe(built);
    expect(mountNode.isConnected).toBe(true);
    expect(player.destroy).not.toHaveBeenCalled();
  });
});
