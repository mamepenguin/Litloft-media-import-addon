/**
 * SPEC-ADDON-162 item 8 / I9: `formatPublishedDate` is the date part of the
 * Watch shelves' byline, shared with the channel view.
 */
import { afterEach, describe, expect, it } from "vitest";

type Format = (publishedAt: string | null, locale: string) => unknown;

async function formatPublishedDate(): Promise<Format> {
  const mod = (await import("@/addons/media_import/lib/publishedDate")) as {
    formatPublishedDate?: Format;
  };
  expect(typeof mod.formatPublishedDate).toBe("function");
  return mod.formatPublishedDate!;
}

describe("SPEC-ADDON-162 formatPublishedDate", () => {
  const originalTz = process.env.TZ;

  afterEach(() => {
    if (originalTz === undefined) delete process.env.TZ;
    else process.env.TZ = originalTz;
  });

  it.each([
    ["en", "Nov 1, 2026"],
    ["ja", "2026年11月1日"],
  ])("SPEC-ADDON-162 I9 formats the date for locale %s", async (locale, expected) => {
    const format = await formatPublishedDate();

    expect(format("20261101", locale)).toBe(expected);
  });

  it.each([
    "20261101",
    "2026-11-01",
    "2026-11-01T00:00:00",
    "2026-11-01T23:30:00-05:00",
    "2026-11-01T01:00:00+09:00",
    "2026-11-01T12:00:00Z",
  ])("SPEC-ADDON-162 I9 takes the calendar date written in %s", async (published) => {
    const format = await formatPublishedDate();

    expect(format(published, "en")).toBe("Nov 1, 2026");
  });

  it.each(["Asia/Tokyo", "America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Pago_Pago"])(
    "SPEC-ADDON-162 I9 does not shift with the browser time zone %s",
    async (tz) => {
      process.env.TZ = tz;
      const format = await formatPublishedDate();

      expect(format("20261101", "en")).toBe("Nov 1, 2026");
      expect(format("2026-11-01T23:30:00-05:00", "en")).toBe("Nov 1, 2026");
    },
  );
});
