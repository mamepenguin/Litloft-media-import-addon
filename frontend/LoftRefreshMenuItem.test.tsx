import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import { ToastProvider } from "@/components/ToastProvider";
import { LOFT_MIME } from "@/lib/playerKind";

const refreshLoft = vi.fn();
vi.mock("@/addons/media_import/api", () => ({
  refreshLoft: (...args: unknown[]) => refreshLoft(...args),
}));

import LoftRefreshMenuItem from "@/addons/media_import/LoftRefreshMenuItem";
import { useLoftRefreshed } from "@/addons/media_import/loftRefresh";

const messages = {
  mediaImport: {
    loftMetadata: {
      refresh: "Refresh metadata",
      refreshStarted: "Refreshing metadata",
      refreshFailed: "Failed to refresh metadata",
    },
  },
};

function Listener({ onRefreshed }: { onRefreshed: () => void }) {
  useLoftRefreshed("f1", onRefreshed);
  return null;
}

function renderItem(
  mimeType: string,
  extra: { onRequestClose?: () => void; onRefreshed?: () => void } = {},
) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ToastProvider>
        <Listener onRefreshed={extra.onRefreshed ?? (() => {})} />
        <LoftRefreshMenuItem
          fileId="f1"
          drive="d"
          mimeType={mimeType}
          onRequestClose={extra.onRequestClose}
        />
      </ToastProvider>
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  refreshLoft.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LoftRefreshMenuItem", () => {
  it.each(["video/mp4", "application/json", ""])(
    "renders nothing for a %s file",
    (mimeType) => {
      renderItem(mimeType);
      expect(screen.queryByRole("menuitem")).toBeNull();
    },
  );

  it("refreshes once, closes the menu, and tells the panel", async () => {
    refreshLoft.mockResolvedValue(undefined);
    const onRequestClose = vi.fn();
    const onRefreshed = vi.fn();
    renderItem(LOFT_MIME, { onRequestClose, onRefreshed });

    fireEvent.click(screen.getByRole("menuitem", { name: "Refresh metadata" }));

    expect(onRequestClose).toHaveBeenCalledTimes(1);
    expect(refreshLoft).toHaveBeenCalledTimes(1);
    expect(refreshLoft).toHaveBeenCalledWith("f1", "d");
    expect(await screen.findByText("Refreshing metadata")).toBeInTheDocument();
    await waitFor(() => expect(onRefreshed).toHaveBeenCalledTimes(1));
  });

  it("reports a failed refresh and does not tell the panel", async () => {
    refreshLoft.mockRejectedValue(new Error("404"));
    const onRefreshed = vi.fn();
    renderItem(LOFT_MIME, { onRefreshed });

    fireEvent.click(screen.getByRole("menuitem", { name: "Refresh metadata" }));

    expect(
      await screen.findByText("Failed to refresh metadata"),
    ).toBeInTheDocument();
    expect(onRefreshed).not.toHaveBeenCalled();
  });
});
