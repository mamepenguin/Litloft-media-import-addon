/**
 * Watch surface behaviour.
 *
 * Spec: `docs/superpowers/specs/2026-08-10-media-import-watch-surface.md`.
 * The lane SQL is covered in the addon's pytest suite; these assert the
 * product rules the UI is responsible for — no backlog counts, lanes
 * that fail independently, completed items subdued but in place, and
 * "watch later" going through core Collections rather than a parallel
 * Media Import list.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import WatchView from "@/addons/media_import/watch";
import type { WatchItem, WatchLane } from "@/addons/media_import/api";

const mockListWatch = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual =
    await vi.importActual<typeof import("@/addons/media_import/api")>(
      "@/addons/media_import/api",
    );
  return {
    ...actual,
    listWatch: (...args: unknown[]) => mockListWatch(...args),
  };
});

const mockAddCollectionItems = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>(
    "@/lib/api",
  );
  return {
    ...actual,
    getCollections: () =>
      Promise.resolve([{ id: "col1", name: "Later", item_count: 0 }]),
    createCollection: vi.fn(),
    addCollectionItems: (...args: unknown[]) =>
      mockAddCollectionItems(...args),
  };
});

function makeItem(overrides: Partial<WatchItem> = {}): WatchItem {
  return {
    file_id: "aaaaaaaaaaaa",
    filename: "Clip.loft",
    title: "Clip",
    thumbnail_path: "thumb.jpg",
    channel: "Chan",
    published_at: "20260801",
    created_at: "2026-08-01T00:00:00",
    duration: 300,
    url: "https://example.test/watch?v=1",
    subscription_id: 1,
    subscription_title: "Chan",
    playback: null,
    ...overrides,
  };
}

/** Route each lane's fetch to its own fixture. */
function laneResponses(byLane: Partial<Record<WatchLane, unknown>>) {
  mockListWatch.mockImplementation((_drive: string, lane: WatchLane) => {
    const value = byLane[lane];
    if (value instanceof Error) return Promise.reject(value);
    return Promise.resolve(value ?? []);
  });
}

describe("WatchView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    laneResponses({});
  });

  it("fetches each lane separately so they page independently", async () => {
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);
    await waitFor(() => expect(mockListWatch).toHaveBeenCalledTimes(3));
    const lanes = mockListWatch.mock.calls.map((c) => c[1]);
    expect(new Set(lanes)).toEqual(new Set(["continue", "regular", "feed"]));
  });

  it("renders each item in the lane it came from", async () => {
    laneResponses({
      regular: [makeItem({ file_id: "regggggggggg", title: "Regular one" })],
      feed: [makeItem({ file_id: "feeddddddddd", title: "Feed one" })],
    });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    await screen.findByText("Regular one");
    const regularLane = await screen.findByTestId("watch-lane-regular");
    const feedLane = await screen.findByTestId("watch-lane-feed");
    expect(regularLane).toHaveTextContent("Regular one");
    expect(regularLane).not.toHaveTextContent("Feed one");
    expect(feedLane).toHaveTextContent("Feed one");
  });

  it("never shows a pending or unread count", async () => {
    laneResponses({
      feed: Array.from({ length: 5 }, (_, i) =>
        makeItem({ file_id: `feed${i}aaaaaaa`, title: `V${i}` }),
      ),
    });
    const { container } = render(
      <WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />,
    );
    await screen.findByText("V0");
    // A bare "5" anywhere would be a backlog figure — spec §2.2 and the
    // "no unread counts" non-goal.
    expect(container.textContent).not.toMatch(/\b5\b/);
  });

  it("subdues completed items without moving them", async () => {
    laneResponses({
      feed: [
        makeItem({
          file_id: "doneeeeeeeee",
          title: "Finished",
          playback: { position: 300, duration: 300, state: "completed" },
        }),
        makeItem({ file_id: "freshhhhhhhh", title: "Fresh" }),
      ],
    });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    const lane = await screen.findByTestId("watch-lane-feed");
    const titles = Array.from(lane.querySelectorAll("h3")).map(
      (h) => h.textContent,
    );
    expect(titles).toEqual(["Finished", "Fresh"]);
    expect(screen.getByText("Watched")).toBeInTheDocument();
  });

  it("shows a resume affordance and progress bar for started videos", async () => {
    laneResponses({
      continue: [
        makeItem({
          file_id: "startedaaaaa",
          title: "Half watched",
          playback: { position: 30, duration: 300, state: "in_progress" },
        }),
      ],
    });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    await screen.findByText("Half watched");
    expect(screen.getByText("Resume")).toBeInTheDocument();
    expect(screen.getByTestId("watch-progress-bar")).toHaveStyle({
      width: "10%",
    });
  });

  it("still renders a video whose playback state is unknown", async () => {
    // The server nulls `playback` both when there is no history row and
    // when reading it failed. Either way the video must appear.
    laneResponses({
      feed: [makeItem({ title: "No badge", playback: null })],
    });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    await screen.findByText("No badge");
    expect(screen.queryByTestId("watch-progress-bar")).toBeNull();
  });

  it("keeps other lanes alive when one fails", async () => {
    laneResponses({
      regular: new Error("regular lane exploded"),
      feed: [makeItem({ title: "Still here" })],
    });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    await screen.findByText("Still here");
    expect(await screen.findByTestId("watch-lane-regular-error")).toBeTruthy();
  });

  it("adds to a core collection instead of a Media Import watch later", async () => {
    laneResponses({ feed: [makeItem({ file_id: "pickmeaaaaaa" })] });
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    fireEvent.click(await screen.findByText("Add to collection"));
    fireEvent.click(await screen.findByText("Later"));

    await waitFor(() =>
      expect(mockAddCollectionItems).toHaveBeenCalledWith("d", "col1", [
        "pickmeaaaaaa",
      ]),
    );
  });

  it("explains how to surface sources when none are", async () => {
    render(
      <WatchView
        drive="d"
        hasSurfacedSources={false}
        onGoToManage={() => {}}
      />,
    );

    const empty = await screen.findByTestId("watch-empty");
    // Never "you have unprocessed imports" — importing for search alone
    // is the normal case (spec §2.1).
    expect(empty.textContent).toContain("Show in recent videos");
    expect(empty.textContent).toContain("searchable");
  });

  it("says nothing is showing right now when sources are surfaced", async () => {
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    const empty = await screen.findByTestId("watch-empty");
    expect(empty.textContent).toContain("Nothing to show right now");
  });

  it("pages a lane without asking for a total", async () => {
    const page1 = Array.from({ length: 24 }, (_, i) =>
      makeItem({ file_id: `p1x${String(i).padStart(9, "0")}`, title: `A${i}` }),
    );
    const page2 = [makeItem({ file_id: "p2xaaaaaaaaa", title: "B0" })];
    mockListWatch.mockImplementation(
      (_drive: string, lane: WatchLane, opts?: { offset?: number }) => {
        if (lane !== "feed") return Promise.resolve([]);
        return Promise.resolve(opts?.offset ? page2 : page1);
      },
    );
    render(<WatchView drive="d" hasSurfacedSources onGoToManage={() => {}} />);

    fireEvent.click(await screen.findByText("Show more"));
    await screen.findByText("B0");
    expect(mockListWatch).toHaveBeenCalledWith("d", "feed", { offset: 24 });
  });
});
