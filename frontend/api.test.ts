import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getLoftMetadata, listWatch, refreshLoft } from "@/addons/media_import/api";

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

describe("refreshLoft", () => {
  it("rejects when the refresh is refused", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("{}", { status: 404 }));

    await expect(refreshLoft("f1", "d")).rejects.toThrow();
  });
});

describe("SPEC-ADDON-091 listWatch reads one keyset page", () => {
  function sentQuery(): URLSearchParams {
    const url = new URL(String(fetchSpy.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/addons/media_import/watch");
    return url.searchParams;
  }

  it("SPEC-ADDON-091 sends the lane and limit, and neither after nor offset when after is not given", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("[]", { status: 200 }));

    await listWatch("d", "feed", { limit: 60 });

    const query = sentQuery();
    expect(query.get("lane")).toBe("feed");
    expect(query.get("limit")).toBe("60");
    expect(query.has("after")).toBe(false);
    expect(query.has("offset")).toBe(false);
  });

  it("SPEC-ADDON-091 sends after when given, and still no offset", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("[]", { status: 200 }));

    await listWatch("d", "regular", { limit: 60, after: "WyIyMDI2Il0" });

    const query = sentQuery();
    expect(query.get("lane")).toBe("regular");
    expect(query.get("limit")).toBe("60");
    expect(query.get("after")).toBe("WyIyMDI2Il0");
    expect(query.has("offset")).toBe(false);
  });
});
