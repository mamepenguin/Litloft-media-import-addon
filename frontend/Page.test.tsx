/**
 * SPEC-ADDON-092: the Media Import page is the management view, with no
 * tabs and no Watch. It reads the subscription list once, only to decide
 * whether the Composer starts expanded.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";

import MediaImportPage from "@/addons/media_import/Page";
import type { Subscription } from "@/addons/media_import/api";

const mockListSubscriptions = vi.fn();
const routeParams = vi.hoisted(() => ({ current: { name: "d" } }));

vi.mock("next/navigation", () => ({
  useParams: () => routeParams.current,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/addons/media_import/api", async () => {
  const actual =
    await vi.importActual<typeof import("@/addons/media_import/api")>(
      "@/addons/media_import/api",
    );
  return {
    ...actual,
    listSubscriptions: (...args: unknown[]) => mockListSubscriptions(...args),
    listWatch: vi.fn().mockResolvedValue([]),
    listActivity: vi.fn().mockResolvedValue([]),
    getSubscriptionSummary: vi.fn().mockResolvedValue({
      total: 0,
      paused: 0,
      syncing: 0,
      healthy: 0,
      attention: 0,
      imported_count: 0,
      failed_count: 0,
    }),
    resolveSubscriptionUrl: vi
      .fn()
      .mockResolvedValue({ kind: "unknown", provider: null, ref: null }),
  };
});

vi.mock("@/lib/api", () => ({
  getFolders: vi.fn().mockResolvedValue([]),
  getFolderTree: vi.fn().mockResolvedValue([]),
  getCollections: vi.fn().mockResolvedValue([]),
  createCollection: vi.fn(),
  addCollectionItems: vi.fn(),
}));

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

const ADDON_ROOT = [
  resolve(process.cwd(), "../addons/media_import"),
  resolve(process.cwd(), "addons/media_import"),
].find((candidate) => existsSync(resolve(candidate, "frontend", "messages", "en.json")))!;

const LOADING = "Loading drive...";

function makeSubscription(mode: Subscription["display_mode"]): Subscription {
  return {
    id: 1,
    provider: "youtube",
    source_kind: "channel",
    source_ref: "UCabc",
    drive: "d",
    folder_path: "",
    title: null,
    is_enabled: true,
    cooldown_minutes: 60,
    include_no_transcript: false,
    last_synced_at: null,
    cooldown_until: null,
    created_at: "2026-05-01T00:00:00",
    running: false,
    avatar_url: null,
    display_title: "Chan",
    display_mode: mode,
  };
}

function expectNoTabsAndNoWatch() {
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(screen.queryByTestId("watch-view")).toBeNull();
  expect(screen.queryByText("Watch")).toBeNull();
  expect(screen.queryByText("Manage")).toBeNull();
}

async function expectManagementView(composer: "composer-collapsed" | "composer-expanded") {
  await waitFor(() => expect(screen.getByTestId(composer)).toBeInTheDocument());
  expect(
    (await screen.findAllByTestId(/^(dashboard-|subscription-card-)/)).length,
  ).toBeGreaterThan(0);
  expect(await screen.findByTestId("activity-empty")).toBeInTheDocument();
  expect(screen.queryByText(LOADING)).toBeNull();
  expectNoTabsAndNoWatch();
}

describe("SPEC-ADDON-092 MediaImportPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeParams.current = { name: "d" };
  });

  it.each(["library", "feed", "regular"] as const)(
    "SPEC-ADDON-092 I9 shows the management view with the Composer collapsed for a %s subscription",
    async (mode) => {
      mockListSubscriptions.mockResolvedValue([makeSubscription(mode)]);
      render(<MediaImportPage />);

      await expectManagementView("composer-collapsed");
    },
  );

  it("SPEC-ADDON-092 I9 expands the Composer when there is no subscription", async () => {
    mockListSubscriptions.mockResolvedValue([]);
    render(<MediaImportPage />);

    await expectManagementView("composer-expanded");
  });

  it("SPEC-ADDON-092 I9 expands the Composer when subscriptions cannot be read", async () => {
    mockListSubscriptions.mockRejectedValue(new Error("offline"));
    render(<MediaImportPage />);

    await expectManagementView("composer-expanded");
  });

  it("SPEC-ADDON-092 §3 moving to another drive starts again from the loading line and reads that drive", async () => {
    mockListSubscriptions.mockImplementation(async (drive: string) =>
      drive === "d" ? [] : new Promise(() => {}),
    );
    const { rerender } = render(<MediaImportPage />);
    await waitFor(() =>
      expect(screen.getByTestId("composer-expanded")).toBeInTheDocument(),
    );

    let settle: (subs: Subscription[]) => void = () => {};
    const driveE = new Promise<Subscription[]>((ok) => {
      settle = ok;
    });
    mockListSubscriptions.mockImplementation((drive: string) =>
      drive === "e" ? driveE : new Promise(() => {}),
    );
    routeParams.current = { name: "e" };
    rerender(<MediaImportPage />);

    // SPEC-ADDON-092 I9: until the read settles, only the loading line.
    await waitFor(() => expect(screen.getByText(LOADING)).toBeInTheDocument());
    expect(screen.queryByTestId("composer-expanded")).toBeNull();
    expect(screen.queryByTestId("composer-collapsed")).toBeNull();
    expect(screen.queryByTestId("activity-empty")).toBeNull();
    expect(screen.queryAllByTestId(/^(dashboard-|subscription-card-)/)).toHaveLength(0);
    expectNoTabsAndNoWatch();
    expect(mockListSubscriptions.mock.calls.some((c) => c[0] === "e")).toBe(true);

    settle([makeSubscription("feed")]);
    await expectManagementView("composer-collapsed");
  });

  it("SPEC-ADDON-092 states the page and the drive in one header", async () => {
    mockListSubscriptions.mockResolvedValue([makeSubscription("feed")]);
    render(<MediaImportPage />);
    await waitFor(() =>
      expect(screen.getByTestId("composer-collapsed")).toBeInTheDocument(),
    );
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("YouTube & Feeds");
    expect(heading.nextElementSibling?.textContent).toBe("d");
    const header = heading.closest("header")!;
    expect(header.querySelector("svg.lucide-rss")).not.toBeNull();
    expect(header.parentElement?.getAttribute("data-page-frame")).toBe("full");
  });

  it("draws the header before the subscription read settles", () => {
    mockListSubscriptions.mockReturnValue(new Promise(() => {}));
    render(<MediaImportPage />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("YouTube & Feeds");
    expect(heading.closest("header")!.parentElement?.getAttribute("data-page-frame")).toBe("full");
    expect(screen.queryByRole("tab")).toBeNull();
  });

  it.each(["ja", "en"])(
    "SPEC-ADDON-092 I9 %s messages hold no mediaImport.nav or mediaImport.watch key",
    (locale) => {
      const messages = JSON.parse(
        readFileSync(resolve(ADDON_ROOT, "frontend", "messages", `${locale}.json`), "utf-8"),
      ) as { mediaImport: Record<string, unknown> };

      expect(messages.mediaImport).not.toHaveProperty("nav");
      expect(messages.mediaImport).not.toHaveProperty("watch");
    },
  );
});
