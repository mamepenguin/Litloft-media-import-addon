/**
 * Tests for the error_kind classification table.
 *
 * After the i18n migration the dictionary no longer carries display
 * text — that lives in messages/{ja,en}.json. We still need to make
 * sure normalisation and the retryable flag round-trip correctly.
 */
import { describe, expect, it } from "vitest";

import {
  isRetryable,
  normalizeErrorKind,
} from "@/addons/media_import/lib/errorMessages";

describe("normalizeErrorKind", () => {
  it("returns null for null input", () => {
    expect(normalizeErrorKind(null)).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(normalizeErrorKind("")).toBeNull();
  });

  it.each([
    "rate_limited",
    "permanent",
    "no_transcript",
    "path_conflict",
    "dismissed",
    "fetch_failed",
  ])("preserves the canonical kind %s", (kind) => {
    expect(normalizeErrorKind(kind)).toBe(kind);
  });

  it("falls back to 'unknown' for unrecognised kinds", () => {
    expect(normalizeErrorKind("future_failure_mode")).toBe("unknown");
  });
});

describe("isRetryable", () => {
  it("returns false for permanent failures", () => {
    expect(isRetryable("permanent")).toBe(false);
  });

  it("returns false for user-issued dismissals", () => {
    expect(isRetryable("dismissed")).toBe(false);
  });

  it("returns true for transient failures", () => {
    expect(isRetryable("rate_limited")).toBe(true);
    expect(isRetryable("no_transcript")).toBe(true);
    expect(isRetryable("path_conflict")).toBe(true);
  });

  it("returns true when error_kind is null (legacy rows without classification)", () => {
    expect(isRetryable(null)).toBe(true);
  });

  it("treats unrecognised kinds as retryable (the 'unknown' bucket)", () => {
    expect(isRetryable("future_failure_mode")).toBe(true);
  });
});
