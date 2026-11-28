import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Loader = typeof import("../loadYouTubeIframeApi");

interface YouTubeWindow extends Window {
  YT?: unknown;
  onYouTubeIframeAPIReady?: () => void;
}

const win = () => window as YouTubeWindow;
const scripts = () =>
  Array.from(document.querySelectorAll<HTMLScriptElement>('script[src*="youtube.com/iframe_api"]'));

async function freshLoader(): Promise<Loader> {
  vi.resetModules();
  return import("../loadYouTubeIframeApi");
}

function settled<T>(promise: Promise<T>, ms = 500): Promise<"resolved" | "rejected" | "pending"> {
  return Promise.race([
    promise.then(() => "resolved" as const, () => "rejected" as const),
    new Promise<"pending">((r) => setTimeout(() => r("pending"), ms)),
  ]);
}

function failScript(script: HTMLScriptElement) {
  script.dispatchEvent(new Event("error"));
}

beforeEach(() => {
  for (const script of scripts()) script.remove();
  delete win().YT;
  delete win().onYouTubeIframeAPIReady;
});

afterEach(() => {
  for (const script of scripts()) script.remove();
  delete win().YT;
  delete win().onYouTubeIframeAPIReady;
});

describe("SPEC-ADDON-191 a failed IFrame API script", () => {
  it("SPEC-ADDON-191 I6: rejects and leaves no script tag behind", async () => {
    const { loadYouTubeIframeApi } = await freshLoader();

    const load = loadYouTubeIframeApi();
    const [script] = scripts();
    expect(script).toBeDefined();
    failScript(script);

    expect(await settled(load)).toBe("rejected");
    expect(scripts()).toHaveLength(0);
  });

  it("SPEC-ADDON-191 §2: a later load in the same page session tries again, fails again, and can then succeed", async () => {
    const { loadYouTubeIframeApi } = await freshLoader();

    const first = loadYouTubeIframeApi();
    failScript(scripts()[0]);
    expect(await settled(first)).toBe("rejected");

    const second = loadYouTubeIframeApi();
    expect(scripts()).toHaveLength(1);
    failScript(scripts()[0]);
    expect(await settled(second)).toBe("rejected");
    expect(scripts()).toHaveLength(0);

    const third = loadYouTubeIframeApi();
    expect(scripts()).toHaveLength(1);
    const api = { Player: class {} };
    win().YT = api;
    win().onYouTubeIframeAPIReady?.();
    expect(await settled(third)).toBe("resolved");
    await expect(third).resolves.toBe(api);
  });
});
