/**
 * Tests for CaptionStatusBadge.
 *
 * Spec: docs/superpowers/specs/2026-04-26-loft-caption-state-visibility.md
 */

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import CaptionStatusBadge from "@/addons/media_import/CaptionStatusBadge";
import type { LoftMetadata } from "@/addons/media_import/api";

const messages = {
  captionStatus: {
    noCaptions: "YouTube has no captions for this video",
    rateLimited: "Retrying caption download (temporary error)",
    permanent:
      "Captions unavailable (video may be private, removed, or region-locked)",
    failed: "Failed to download captions",
    notAttempted: "Caption status not yet checked",
    retryHint: "Click to retry",
    retrying: "Retrying...",
  },
};

function makeMetadata(overrides: Partial<LoftMetadata> = {}): LoftMetadata {
  return {
    provider: "youtube",
    url: "https://www.youtube.com/watch?v=abc",
    description: null,
    channel: null,
    published_at: null,
    language: null,
    has_captions: true,
    captions_downloaded: false,
    caption_error_kind: null,
    fetched_at: "2026-04-26T00:00:00Z",
    fetch_error: null,
    ...overrides,
  };
}

function renderBadge(
  metadata: LoftMetadata,
  props: { onRetry?: () => void; isRetrying?: boolean } = {},
) {
  return render(
    <NextIntlClientProvider locale="ja" messages={messages}>
      <CaptionStatusBadge metadata={metadata} {...props} />
    </NextIntlClientProvider>,
  );
}

describe("CaptionStatusBadge", () => {
  it("renders nothing when captions were downloaded successfully", () => {
    const { container } = renderBadge(
      makeMetadata({ captions_downloaded: true }),
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the not-attempted label when fetched_at is null", () => {
    const { getByText } = renderBadge(
      makeMetadata({ fetched_at: null, has_captions: false }),
    );
    expect(getByText("Caption status not yet checked")).toBeTruthy();
  });

  it("shows the no-captions label when YouTube has no captions", () => {
    const { getByText } = renderBadge(
      makeMetadata({ has_captions: false }),
    );
    expect(getByText("YouTube has no captions for this video")).toBeTruthy();
  });

  it("shows the permanent-failure label when caption_error_kind is 'permanent'", () => {
    const { getByText } = renderBadge(
      makeMetadata({ caption_error_kind: "permanent" }),
    );
    expect(
      getByText(
        "Captions unavailable (video may be private, removed, or region-locked)",
      ),
    ).toBeTruthy();
  });

  it("shows the rate-limited label when caption_error_kind is 'rate_limited'", () => {
    const { getByText } = renderBadge(
      makeMetadata({ caption_error_kind: "rate_limited" }),
    );
    expect(
      getByText("Retrying caption download (temporary error)"),
    ).toBeTruthy();
  });

  it("shows the generic-failure label when has_captions is true but error_kind is null", () => {
    const { getByText } = renderBadge(
      makeMetadata({
        has_captions: true,
        captions_downloaded: false,
        caption_error_kind: null,
      }),
    );
    expect(getByText("Failed to download captions")).toBeTruthy();
  });

  it("prioritises captions_downloaded=true over every other state", () => {
    const { container } = renderBadge(
      makeMetadata({
        captions_downloaded: true,
        caption_error_kind: "permanent",
        has_captions: false,
      }),
    );
    expect(container.firstChild).toBeNull();
  });

  describe("retry behaviour", () => {
    it("renders generic-failure as a button when onRetry is supplied", () => {
      const onRetry = vi.fn();
      const { getByRole } = renderBadge(
        makeMetadata({ caption_error_kind: null }),
        { onRetry },
      );
      const btn = getByRole("button");
      expect(btn).toBeTruthy();
      fireEvent.click(btn);
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it("renders rate_limited as a button when onRetry is supplied", () => {
      const onRetry = vi.fn();
      const { getByRole } = renderBadge(
        makeMetadata({ caption_error_kind: "rate_limited" }),
        { onRetry },
      );
      fireEvent.click(getByRole("button"));
      expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it("does NOT render permanent failure as a button (retry would be wasted)", () => {
      const onRetry = vi.fn();
      const { queryByRole } = renderBadge(
        makeMetadata({ caption_error_kind: "permanent" }),
        { onRetry },
      );
      expect(queryByRole("button")).toBeNull();
    });

    it("does NOT render no-captions as a button (nothing to retry)", () => {
      const onRetry = vi.fn();
      const { queryByRole } = renderBadge(
        makeMetadata({ has_captions: false }),
        { onRetry },
      );
      expect(queryByRole("button")).toBeNull();
    });

    it("does NOT render not-attempted as a button (handled by background fetcher)", () => {
      const onRetry = vi.fn();
      const { queryByRole } = renderBadge(
        makeMetadata({ fetched_at: null, has_captions: false }),
        { onRetry },
      );
      expect(queryByRole("button")).toBeNull();
    });

    it("falls back to a non-interactive surface when onRetry is omitted", () => {
      const { queryByRole, getByRole } = renderBadge(
        makeMetadata({ caption_error_kind: null }),
      );
      expect(queryByRole("button")).toBeNull();
      expect(getByRole("status")).toBeTruthy();
    });

    it("shows the retrying label and disables the button while in flight", () => {
      const onRetry = vi.fn();
      const { getByRole, getByText } = renderBadge(
        makeMetadata({ caption_error_kind: "rate_limited" }),
        { onRetry, isRetrying: true },
      );
      const btn = getByRole("button") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(getByText("Retrying...")).toBeTruthy();
      fireEvent.click(btn);
      expect(onRetry).not.toHaveBeenCalled();
    });
  });
});
