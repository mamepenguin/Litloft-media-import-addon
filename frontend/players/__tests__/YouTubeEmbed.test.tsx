import { describe, expect, it } from "vitest";
import { extractYouTubeId, isAdDuration } from "../YouTubeEmbed";

describe("isAdDuration", () => {
  it("treats a reported duration close to our metadata as the real video", () => {
    expect(isAdDuration(600, 600)).toBe(false);
    // yt-dlp metadata and the player routinely disagree by ~1s.
    expect(isAdDuration(601, 600)).toBe(false);
    expect(isAdDuration(599, 600)).toBe(false);
  });

  it("treats a wildly different duration as an ad", () => {
    // Pre-roll ads are typically 5-30s against a video of minutes.
    expect(isAdDuration(15, 600)).toBe(true);
    expect(isAdDuration(603, 600)).toBe(true);
  });

  it("disables detection when we have no trustworthy duration (fail-open)", () => {
    // Being wrong here disables the seek bar mid-video, which is worse
    // than letting an ad desync the clock.
    for (const hint of [null, undefined, 0, -5, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(isAdDuration(15, hint)).toBe(false);
    }
  });

  it("reports no ad while the player has not published a duration yet", () => {
    for (const reported of [0, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(isAdDuration(reported, 600)).toBe(false);
    }
  });
});

describe("extractYouTubeId", () => {
  it("extracts id from watch URL", () => {
    expect(extractYouTubeId("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toBe(
      "dQw4w9WgXcQ",
    );
  });

  it("extracts id from youtu.be short URL", () => {
    expect(extractYouTubeId("https://youtu.be/dQw4w9WgXcQ")).toBe(
      "dQw4w9WgXcQ",
    );
  });

  it("extracts id from /embed/ URL", () => {
    expect(
      extractYouTubeId("https://www.youtube.com/embed/dQw4w9WgXcQ"),
    ).toBe("dQw4w9WgXcQ");
  });

  it("extracts id from /shorts/ URL", () => {
    expect(
      extractYouTubeId("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
    ).toBe("dQw4w9WgXcQ");
  });

  it("rejects lookalike hosts that contain youtube.com in path", () => {
    expect(
      extractYouTubeId("https://evil.example/youtube.com/watch?v=dQw4w9WgXcQ"),
    ).toBeNull();
    expect(
      extractYouTubeId("https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"),
    ).toBeNull();
  });

  it("returns null for non-youtube URL", () => {
    expect(extractYouTubeId("https://vimeo.com/123")).toBeNull();
    expect(extractYouTubeId("https://example.com/x")).toBeNull();
  });

  it("returns null for malformed URL", () => {
    expect(extractYouTubeId("not-a-url")).toBeNull();
    expect(extractYouTubeId("")).toBeNull();
  });
});
