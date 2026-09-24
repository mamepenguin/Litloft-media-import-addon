import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getLoftMetadata, refreshLoft } from "@/addons/media_import/api";

const fetchSpy = vi.fn(
  async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response("{}", { status: 200 }),
);

beforeEach(() => {
  fetchSpy.mockClear();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function sentHeaders(): Record<string, string> {
  return fetchSpy.mock.calls[0][1]?.headers as Record<string, string>;
}

describe("loft link calls carry the drive scope", () => {
  it("getLoftMetadata sends the percent-encoded drive", async () => {
    await getLoftMetadata("f1", "動画");

    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      "/api/addons/media_import/link/f1/metadata",
    );
    expect(sentHeaders()).toEqual({ "X-Lit-Drive": "%E5%8B%95%E7%94%BB" });
  });

  it("refreshLoft sends the percent-encoded drive", async () => {
    await refreshLoft("f1", "動画");

    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      "/api/addons/media_import/link/f1/refresh",
    );
    expect(fetchSpy.mock.calls[0][1]?.method).toBe("POST");
    expect(sentHeaders()).toEqual({ "X-Lit-Drive": "%E5%8B%95%E7%94%BB" });
  });
});
