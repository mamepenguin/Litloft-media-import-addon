/**
 * SPEC-ADDON-091: Media Import supplies its two video app shelves through
 * `slotPageProviders["media-import-regular"]` and
 * `slotPageProviders["media-import-feed"]`, paging each lane with a keyset
 * cursor and giving every item a byline. SPEC-ADDON-088's list providers are
 * gone (SPEC-ADDON-091 I8).
 *
 * `listWatch` is replaced by a fake keyset lane: every row's cursor is
 * `c:<file_id>`, and a read with `after` returns the rows that follow that one.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const listWatch = vi.fn();

vi.mock("@/addons/media_import/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/addons/media_import/api")>()),
  listWatch: (...args: unknown[]) => listWatch(...args),
}));

import * as slots from "@/addons/media_import/slots";

type Restrict = (ids: string[]) => Promise<string[]>;
type PageItem = { id: string; byline?: string };
type Page = { items: PageItem[]; cursor: string | null };
type PageArgs = {
  drive: string;
  locale: string;
  limit: number;
  cursor: string | null;
  restrict: Restrict;
};
type PageProvider = (args: PageArgs) => Promise<Page>;

const ADDON_ROOT = [
  resolve(process.cwd(), "../addons/media_import"),
  resolve(process.cwd(), "addons/media_import"),
].find((candidate) => existsSync(resolve(candidate, "frontend", "messages", "en.json")))!;

const DRIVE = "videos";
const READ = 60;

const LANES = [
  ["media-import-regular", "regular"],
  ["media-import-feed", "feed"],
] as const;

type Row = { file_id: string; channel: string | null; published_at: string | null };

function provider(id: string): PageProvider {
  const providers = (slots as { slotPageProviders?: Record<string, PageProvider> })
    .slotPageProviders;
  const fn = providers?.[id];
  expect(typeof fn).toBe("function");
  return fn!;
}

const cursorOf = (fileId: string) => `c:${fileId}`;

function watchItem(row: Row) {
  return {
    file_id: row.file_id,
    filename: `${row.file_id}.loft`,
    title: row.file_id,
    thumbnail_path: null,
    channel: row.channel,
    published_at: row.published_at,
    created_at: "2026-08-01T00:00:00",
    duration: 300,
    url: "https://example.test/watch",
    subscription_id: 1,
    subscription_title: "Chan",
    cursor: cursorOf(row.file_id),
  };
}

function rows(prefix: string, count: number): Row[] {
  return Array.from({ length: count }, (_, i) => ({
    file_id: `${prefix}${String(i).padStart(4, "0")}`,
    channel: "Chan",
    published_at: "20261101",
  }));
}

const idsOf = (lane: Row[]) => lane.map((r) => r.file_id);

let lanes: Record<string, Row[]>;

function serveLanes() {
  listWatch.mockImplementation(
    async (_drive: string, lane: string, opts: { limit: number; after?: string }) => {
      const all = lanes[lane] ?? [];
      const start =
        opts.after === undefined
          ? 0
          : all.findIndex((r) => cursorOf(r.file_id) === opts.after) + 1;
      return all.slice(start, start + opts.limit).map(watchItem);
    },
  );
}

const acceptAll: Restrict = async (ids) => ids;

function call(id: string, args: Partial<PageArgs> = {}): Promise<Page> {
  return provider(id)({
    drive: DRIVE,
    locale: "en",
    limit: 12,
    cursor: null,
    restrict: acceptAll,
    ...args,
  });
}

beforeEach(() => {
  lanes = { regular: rows("reg", 30), feed: rows("feed", 30) };
  listWatch.mockReset();
  serveLanes();
});

describe("SPEC-ADDON-091 reading the lane", () => {
  it.each(LANES)("SPEC-ADDON-091 %s starts at the lane's top for a null cursor, 60 rows at a time", async (id, lane) => {
    await call(id);

    expect(listWatch).toHaveBeenCalledTimes(1);
    const [drive, calledLane, opts] = listWatch.mock.calls[0];
    expect(drive).toBe(DRIVE);
    expect(calledLane).toBe(lane);
    expect(opts.limit).toBe(READ);
    expect(opts.after).toBeUndefined();
    expect(opts).not.toHaveProperty("offset");
  });

  it.each(LANES)("SPEC-ADDON-091 %s resumes after the cursor it is given", async (id, lane) => {
    const all = lanes[lane];

    const out = await call(id, { cursor: cursorOf(all[4].file_id) });

    expect(listWatch.mock.calls[0][2].after).toBe(cursorOf(all[4].file_id));
    expect(out.items.map((i) => i.id)).toEqual(idsOf(all.slice(5, 17)));
  });

  it.each(LANES)("SPEC-ADDON-088 I9 SPEC-ADDON-091 I4 %s returns no id of the other lane", async (id, lane) => {
    const other = idsOf(lane === "regular" ? lanes.feed : lanes.regular);

    const out = await call(id, { limit: 60 });

    expect(out.items.length).toBeGreaterThan(0);
    expect(out.items.some((i) => other.includes(i.id))).toBe(false);
  });
});

describe("SPEC-ADDON-091 I4 restrict", () => {
  it.each(LANES)("SPEC-ADDON-091 I4 %s passes each read's ids through restrict once and keeps lane order", async (id, lane) => {
    lanes[lane] = rows("r", 100);
    const all = idsOf(lanes[lane]);
    const accepted = new Set(all.filter((_, i) => i % 3 === 0));
    // Answers out of order: the provider, not restrict, owns the order.
    const restrict = vi.fn<Restrict>(async (ids) => ids.filter((x) => accepted.has(x)).reverse());

    const out = await call(id, { limit: 60, restrict });

    expect(restrict).toHaveBeenCalledTimes(2);
    expect(restrict.mock.calls[0][0]).toEqual(all.slice(0, 60));
    expect(restrict.mock.calls[1][0]).toEqual(all.slice(60, 100));
    expect(out.items.map((i) => i.id)).toEqual(all.filter((x) => accepted.has(x)));
  });

  it.each(LANES)("SPEC-ADDON-091 I4 %s never returns an id restrict did not accept", async (id, lane) => {
    const restrict: Restrict = async (ids) => ids.filter((x) => x.endsWith("7"));

    const out = await call(id, { limit: 60, restrict });

    expect(out.items.map((i) => i.id)).toEqual(idsOf(lanes[lane]).filter((x) => x.endsWith("7")));
  });

  it.each(LANES)("SPEC-ADDON-091 I4 %s returns each accepted row exactly once across cursors", async (id, lane) => {
    lanes[lane] = rows("r", 250);
    const all = idsOf(lanes[lane]);
    const accepted = new Set(all.filter((_, i) => i % 7 !== 3));
    const restrict: Restrict = async (ids) => ids.filter((x) => accepted.has(x));

    const seen: string[] = [];
    let cursor: string | null = null;
    for (let page = 0; page < 50; page++) {
      const out: Page = await call(id, { limit: 12, cursor, restrict });
      seen.push(...out.items.map((i) => i.id));
      cursor = out.cursor;
      if (cursor === null) break;
    }

    expect(cursor).toBeNull();
    expect(seen).toEqual(all.filter((x) => accepted.has(x)));
  });
});

describe("SPEC-ADDON-091 I5 I6 where a call stops and what cursor it returns", () => {
  it.each(LANES)("SPEC-ADDON-091 I5 %s returns the cursor of the limit-th taken row, and the next call resumes after it", async (id, lane) => {
    lanes[lane] = rows("r", 200);
    const all = idsOf(lanes[lane]);
    const restrict: Restrict = async (ids) => ids.filter((x) => all.indexOf(x) % 2 === 0);

    const first = await call(id, { limit: 12, restrict });

    expect(listWatch).toHaveBeenCalledTimes(1);
    expect(first.items.map((i) => i.id)).toEqual(all.filter((_, i) => i % 2 === 0 && i <= 22));
    expect(first.cursor).toBe(cursorOf(all[22]));

    listWatch.mockClear();
    const second = await call(id, { limit: 12, cursor: first.cursor, restrict });
    expect(listWatch.mock.calls[0][2].after).toBe(cursorOf(all[22]));
    expect(second.items[0].id).toBe(all[24]);
  });

  it.each(LANES)("SPEC-ADDON-091 I5 %s returns null once a short read is examined to its last row", async (id, lane) => {
    const out = await call(id, { limit: 60 });

    expect(listWatch).toHaveBeenCalledTimes(1);
    expect(out.items.map((i) => i.id)).toEqual(idsOf(lanes[lane]));
    expect(out.cursor).toBeNull();
  });

  it.each(LANES)("SPEC-ADDON-091 I5 %s keeps a cursor when the limit is reached before a short read's last row", async (id, lane) => {
    const all = idsOf(lanes[lane]);

    const out = await call(id, { limit: 12 });

    expect(out.items.map((i) => i.id)).toEqual(all.slice(0, 12));
    expect(out.cursor).toBe(cursorOf(all[11]));
  });

  it.each(LANES)("SPEC-ADDON-091 I5 %s returns null when the limit-th taken row is a short read's last row", async (id, lane) => {
    lanes[lane] = rows("r", 12);

    const out = await call(id, { limit: 12 });

    expect(out.items.map((i) => i.id)).toEqual(idsOf(lanes[lane]));
    expect(out.cursor).toBeNull();
  });

  it.each(LANES)("SPEC-ADDON-091 I5 %s on an empty lane reads once and ends", async (id, lane) => {
    lanes[lane] = [];

    const out = await call(id);

    expect(listWatch).toHaveBeenCalledTimes(1);
    expect(out).toEqual({ items: [], cursor: null });
  });

  it.each(LANES)("SPEC-ADDON-091 I5 %s on a lane of exactly 60 rows reads again and then ends", async (id, lane) => {
    lanes[lane] = rows("r", 60);

    const out = await call(id, { limit: 100 });

    expect(listWatch).toHaveBeenCalledTimes(2);
    expect(listWatch.mock.calls[1][2].after).toBe(cursorOf(lanes[lane][59].file_id));
    expect(out.items).toHaveLength(60);
    expect(out.cursor).toBeNull();
  });

  it.each(LANES)("SPEC-ADDON-091 I6 %s makes no further read once the limit is taken on a full read's last row", async (id, lane) => {
    lanes[lane] = rows("r", 200);

    const out = await call(id, { limit: 60 });

    expect(listWatch).toHaveBeenCalledTimes(1);
    expect(out.items).toHaveLength(60);
    expect(out.cursor).toBe(cursorOf(lanes[lane][59].file_id));
  });

  it.each(LANES)("SPEC-ADDON-091 I6 %s makes no further read after a short read", async (id, lane) => {
    lanes[lane] = rows("r", 70);

    const out = await call(id, { limit: 60, restrict: async () => [] });

    expect(listWatch).toHaveBeenCalledTimes(2);
    expect(out).toEqual({ items: [], cursor: null });
  });

  it.each(LANES)("SPEC-ADDON-091 I6 %s stops at 10 reads and returns the cursor of the last row examined", async (id, lane) => {
    lanes[lane] = rows("r", 1000);

    const out = await call(id, { limit: 12, restrict: async () => [] });

    expect(listWatch).toHaveBeenCalledTimes(10);
    expect(out.items).toEqual([]);
    expect(out.cursor).toBe(cursorOf(lanes[lane][599].file_id));
  });

  it.each(LANES)("SPEC-ADDON-091 I6 %s at the read bound returns what it took with fewer than limit", async (id, lane) => {
    lanes[lane] = rows("r", 1000);
    const all = idsOf(lanes[lane]);
    const wanted = new Set([all[5], all[300], all[599], all[600]]);
    const restrict: Restrict = async (ids) => ids.filter((x) => wanted.has(x));

    const out = await call(id, { limit: 12, restrict });

    expect(listWatch).toHaveBeenCalledTimes(10);
    expect(out.items.map((i) => i.id)).toEqual([all[5], all[300], all[599]]);
    expect(out.cursor).toBe(cursorOf(all[599]));

    listWatch.mockClear();
    const next = await call(id, { limit: 12, cursor: out.cursor, restrict });
    expect(next.items.map((i) => i.id)).toEqual([all[600]]);
  });
});

describe("SPEC-ADDON-091 failures", () => {
  it.each(LANES)("SPEC-ADDON-091 %s rejects when the first read fails", async (id) => {
    listWatch.mockRejectedValue(new Error("watch failed"));

    await expect(call(id)).rejects.toThrow();
  });

  it.each(LANES)("SPEC-ADDON-091 %s rejects when a later read fails", async (id, lane) => {
    lanes[lane] = rows("r", 200);
    const served = listWatch.getMockImplementation()!;
    let reads = 0;
    listWatch.mockImplementation(async (...args: unknown[]) => {
      reads += 1;
      if (reads === 2) throw new Error("watch failed");
      return served(...args);
    });

    await expect(call(id, { restrict: async () => [] })).rejects.toThrow();
    expect(reads).toBe(2);
  });

  it.each(LANES)("SPEC-ADDON-091 %s rejects when restrict rejects", async (id) => {
    const restrict = vi.fn<Restrict>(async () => {
      throw new Error("restrict failed");
    });

    await expect(call(id, { restrict })).rejects.toThrow();
  });
});

describe("SPEC-ADDON-091 I7 bylines", () => {
  const originalTz = process.env.TZ;

  afterEach(() => {
    if (originalTz === undefined) delete process.env.TZ;
    else process.env.TZ = originalTz;
  });

  async function bylineFor(row: Partial<Row>, locale = "en", lane = "feed") {
    lanes[lane] = [{ file_id: "one", channel: "Chan", published_at: "20261101", ...row }];
    const id = lane === "feed" ? "media-import-feed" : "media-import-regular";
    const out = await call(id, { locale });
    expect(out.items.map((i) => i.id)).toEqual(["one"]);
    return out.items[0].byline;
  }

  it.each(LANES)("SPEC-ADDON-091 I7 %s gives channel and date joined by a middle dot", async (_id, lane) => {
    expect(await bylineFor({ channel: "Chan", published_at: "20261101" }, "en", lane)).toBe(
      "Chan · Nov 1, 2026",
    );
  });

  it.each([
    ["en", "Chan · Nov 1, 2026"],
    ["ja", "Chan · 2026年11月1日"],
  ])("SPEC-ADDON-091 I7 formats the date for locale %s", async (locale, expected) => {
    expect(await bylineFor({ published_at: "20261101" }, locale)).toBe(expected);
  });

  it.each([
    ["20261101", "YYYYMMDD"],
    ["2026-11-01", "ISO date"],
    ["2026-11-01T00:00:00", "ISO datetime without offset"],
    ["2026-11-01T23:30:00-05:00", "ISO datetime whose UTC instant is the next day"],
    ["2026-11-01T01:00:00+09:00", "ISO datetime whose UTC instant is the previous day"],
    ["2026-11-01T12:00:00Z", "ISO datetime in UTC"],
  ])("SPEC-ADDON-091 I7 takes the calendar date written in %s (%s)", async (published) => {
    expect(await bylineFor({ published_at: published })).toBe("Chan · Nov 1, 2026");
  });

  it.each(["Asia/Tokyo", "America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Pago_Pago"])(
    "SPEC-ADDON-091 I7 the date does not shift with the browser time zone %s",
    async (tz) => {
      process.env.TZ = tz;

      expect(await bylineFor({ published_at: "20261101" })).toBe("Chan · Nov 1, 2026");
      expect(await bylineFor({ published_at: "2026-11-01" })).toBe("Chan · Nov 1, 2026");
      expect(await bylineFor({ published_at: "2026-11-01T23:30:00-05:00" })).toBe(
        "Chan · Nov 1, 2026",
      );
    },
  );

  it("SPEC-ADDON-091 I7 trims the channel", async () => {
    expect(await bylineFor({ channel: "  Chan  " })).toBe("Chan · Nov 1, 2026");
  });

  it("SPEC-ADDON-091 I7 is the date alone without a channel", async () => {
    expect(await bylineFor({ channel: null })).toBe("Nov 1, 2026");
  });

  it("SPEC-ADDON-091 I7 is the channel alone without a publish date", async () => {
    expect(await bylineFor({ published_at: null })).toBe("Chan");
  });

  it.each(["not a date", "unknown"])(
    "SPEC-ADDON-091 I7 leaves out a publish date that does not parse (%s)",
    async (published) => {
      expect(await bylineFor({ published_at: published })).toBe("Chan");
    },
  );

  it("SPEC-ADDON-091 I7 has no byline without channel or date", async () => {
    expect(await bylineFor({ channel: null, published_at: null })).toBeUndefined();
  });
});

describe("SPEC-ADDON-091 I8 no list providers", () => {
  it.each(LANES)("SPEC-ADDON-091 I8 slots.ts exports no slotProviders entry for %s", (id) => {
    const providers = (slots as { slotProviders?: Record<string, unknown> }).slotProviders;
    expect(providers?.[id]).toBeUndefined();
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
