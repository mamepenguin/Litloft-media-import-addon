/**
 * Tests for the error_kind translation table.
 *
 * The dictionary is the single source of truth the UI consults to
 * decide what to show next to a failed item, including whether to
 * surface a retry button.
 */
import { describe, expect, it } from "vitest";

import {
  describeError,
  isRetryable,
} from "@/addons/media_import/lib/errorMessages";

describe("describeError", () => {
  it("returns null for null input", () => {
    expect(describeError(null)).toBeNull();
  });

  it("returns null for empty string", () => {
    // Mirrors error_kind being NULL in the DB.
    expect(describeError("")).toBeNull();
  });

  it.each([
    "rate_limited",
    "permanent",
    "no_transcript",
    "path_conflict",
    "dismissed",
  ])("has a label and hint for %s", (kind) => {
    const msg = describeError(kind);
    expect(msg).not.toBeNull();
    expect(msg!.label.length).toBeGreaterThan(0);
    expect(msg!.hint.length).toBeGreaterThan(0);
  });

  it("falls back to a generic message for unknown kinds", () => {
    const msg = describeError("future_failure_mode");
    expect(msg).not.toBeNull();
    expect(msg!.retryable).toBe(true);
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

  it("returns false when error_kind is null", () => {
    expect(isRetryable(null)).toBe(false);
  });
});
