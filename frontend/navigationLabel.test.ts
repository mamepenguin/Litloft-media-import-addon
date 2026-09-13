import { describe, expect, it } from "vitest";

import en from "./messages/en.json";
import ja from "./messages/ja.json";

describe("the YouTube & Feeds navigation label", () => {
  it("is carried under the key the addon declares, in both locales", () => {
    expect(en.mediaImport.sidebar.label).toBe("YouTube & Feeds");
    expect(ja.mediaImport.sidebar.label).toBe("YouTube とフィード");
  });
});
