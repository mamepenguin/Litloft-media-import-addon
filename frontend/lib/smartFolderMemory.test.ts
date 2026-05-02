/**
 * Tests for the localStorage-backed last-folder memory.
 *
 * jsdom 25 ships a non-functional ``localStorage`` shim that no-ops
 * setItem; we replace it with an in-memory Storage so the round-trip
 * actually persists for the duration of each test.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  forgetFolder,
  getLastFolder,
  rememberFolder,
  _resetMemoryForTests,
} from "@/addons/media_import/lib/smartFolderMemory";

function makeMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
    key: (index: number) => Array.from(data.keys())[index] ?? null,
  };
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    value: makeMemoryStorage(),
    configurable: true,
  });
});

afterEach(() => {
  _resetMemoryForTests();
});

describe("smartFolderMemory", () => {
  it("returns null when no entry has been recorded", () => {
    expect(getLastFolder("d", "youtube", "channel")).toBeNull();
  });

  it("round-trips a single (drive, provider, kind) tuple", () => {
    rememberFolder("d", "youtube", "channel", "videos/yt");
    expect(getLastFolder("d", "youtube", "channel")).toBe("videos/yt");
  });

  it("isolates by drive", () => {
    rememberFolder("d1", "youtube", "channel", "a");
    rememberFolder("d2", "youtube", "channel", "b");
    expect(getLastFolder("d1", "youtube", "channel")).toBe("a");
    expect(getLastFolder("d2", "youtube", "channel")).toBe("b");
  });

  it("isolates channel from playlist", () => {
    rememberFolder("d", "youtube", "channel", "ch-folder");
    rememberFolder("d", "youtube", "playlist", "pl-folder");
    expect(getLastFolder("d", "youtube", "channel")).toBe("ch-folder");
    expect(getLastFolder("d", "youtube", "playlist")).toBe("pl-folder");
  });

  it("forgetFolder removes only the targeted entry", () => {
    rememberFolder("d", "youtube", "channel", "ch");
    rememberFolder("d", "youtube", "playlist", "pl");
    forgetFolder("d", "youtube", "channel");
    expect(getLastFolder("d", "youtube", "channel")).toBeNull();
    expect(getLastFolder("d", "youtube", "playlist")).toBe("pl");
  });

  it("survives re-reading from storage", () => {
    rememberFolder("d", "youtube", "channel", "x");
    // Force a re-read (no in-memory cache).
    expect(getLastFolder("d", "youtube", "channel")).toBe("x");
  });

  it("treats malformed JSON as empty without throwing", () => {
    window.localStorage.setItem(
      "media_import.last_folder_v1",
      "{not json",
    );
    expect(getLastFolder("d", "youtube", "channel")).toBeNull();
  });
});
