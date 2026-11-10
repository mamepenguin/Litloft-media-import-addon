/**
 * SPEC-ADDON-088: Media Import supplies two video app Discover shelves from
 * its Watch lanes, through `slotProviders["media-import-regular"]` and
 * `slotProviders["media-import-feed"]`.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const listWatch = vi.fn();

vi.mock("@/addons/media_import/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/addons/media_import/api")>()),
  listWatch: (...args: unknown[]) => listWatch(...args),
}));

import * as slots from "@/addons/media_import/slots";

type Restrict = (ids: string[]) => Promise<string[]>;
type Provider = (args: { drive: string; limit: number; restrict: Restrict }) => Promise<string[]>;

const ADDON_ROOT = [
  resolve(process.cwd(), "../addons/media_import"),
  resolve(process.cwd(), "addons/media_import"),
].find((candidate) => existsSync(resolve(candidate, "frontend", "messages", "en.json")))!;

const DRIVE = "videos";

const LANES = [
  ["media-import-regular", "regular"],
  ["media-import-feed", "feed"],
] as const;

function provider(id: string): Provider {
  const providers = (slots as { slotProviders?: Record<string, Provider> }).slotProviders;
  const fn = providers?.[id];
  expect(typeof fn).toBe("function");
  return fn!;
}

const watchItem = (file_id: string) => ({
  file_id,
  filename: `${file_id}.loft`,
  title: file_id,
  thumbnail_path: null,
  channel: "Chan",
  published_at: "20260801",
  created_at: "2026-08-01T00:00:00",
  duration: 300,
  url: "https://example.test/watch",
  subscription_id: 1,
  subscription_title: "Chan",
  playback: null,
});

const laneIds: Record<string, string[]> = {
  regular: ["reg3", "reg1", "reg2"],
  feed: ["feed2", "feed9", "feed1"],
};

beforeEach(() => {
  listWatch.mockReset().mockImplementation(async (_drive: string, lane: string) =>
    (laneIds[lane] ?? []).map(watchItem),
  );
});

describe("SPEC-ADDON-088 I9 the Watch lane providers", () => {
  it.each(LANES)("SPEC-ADDON-088 I9 %s reads its own lane once, 60 at most, for the drive", async (id, lane) => {
    await provider(id)({ drive: DRIVE, limit: 12, restrict: async (ids) => ids });

    expect(listWatch).toHaveBeenCalledTimes(1);
    expect(listWatch.mock.calls[0][0]).toBe(DRIVE);
    expect(listWatch.mock.calls[0][1]).toBe(lane);
    expect(listWatch.mock.calls[0][2]).toMatchObject({ limit: 60 });
  });

  it.each(LANES)("SPEC-ADDON-088 I9 %s passes the lane through restrict once and returns it in lane order", async (id, lane) => {
    const restrict = vi.fn<Restrict>(async (ids) => ids.filter((x) => !x.endsWith("1")));

    const out = await provider(id)({ drive: DRIVE, limit: 12, restrict });

    expect(restrict).toHaveBeenCalledTimes(1);
    expect(restrict.mock.calls[0][0]).toEqual(laneIds[lane]);
    expect(out).toEqual(laneIds[lane].filter((x) => !x.endsWith("1")));
  });

  it.each(LANES)("SPEC-ADDON-088 I9 %s returns no id of the other lane", async (id, lane) => {
    const other = lane === "regular" ? laneIds.feed : laneIds.regular;

    const out = await provider(id)({ drive: DRIVE, limit: 12, restrict: async (ids) => ids });

    expect(out.some((x) => other.includes(x))).toBe(false);
  });

  it.each(LANES)("SPEC-ADDON-088 %s rejects when the watch request fails", async (id) => {
    listWatch.mockRejectedValue(new Error("watch failed"));

    await expect(provider(id)({ drive: DRIVE, limit: 12, restrict: async (ids) => ids })).rejects.toThrow();
  });

  it.each(LANES)("SPEC-ADDON-088 %s rejects when restrict rejects", async (id) => {
    const restrict = vi.fn<Restrict>(async () => {
      throw new Error("restrict failed");
    });

    await expect(provider(id)({ drive: DRIVE, limit: 12, restrict })).rejects.toThrow();
  });
});

describe("SPEC-ADDON-088 shelf labels", () => {
  it.each([
    ["ja", "よく見るソース", "購読の新着"],
    ["en", "Regular sources", "New from subscriptions"],
  ])("SPEC-ADDON-088 %s messages hold the two shelf labels", (locale, regular, feed) => {
    const messages = JSON.parse(
      readFileSync(resolve(ADDON_ROOT, "frontend", "messages", `${locale}.json`), "utf-8"),
    ) as { mediaImport?: { slots?: Record<string, string> } };

    expect(messages.mediaImport?.slots?.videoRegular).toBe(regular);
    expect(messages.mediaImport?.slots?.videoFeed).toBe(feed);
  });
});
