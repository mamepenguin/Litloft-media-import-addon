import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import ActivityFeed from "@/addons/media_import/ActivityFeed";
import type { ActivityEntry } from "@/addons/media_import/api";

const mockListActivity = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    listActivity: (...args: unknown[]) => mockListActivity(...args),
  };
});

vi.mock("@/hooks/useWebSocket", () => ({
  useWebSocket: () => null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function entry(overrides: Partial<ActivityEntry>): ActivityEntry {
  return {
    file_id: "f1",
    filename: "Video.loft",
    thumbnail_path: null,
    channel: null,
    published_at: null,
    created_at: new Date().toISOString(),
    source: "single",
    subscription_id: null,
    subscription_title: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockListActivity.mockResolvedValue([]);
});

describe("ActivityFeed", () => {
  it("renders empty state when there is no activity", async () => {
    render(<ActivityFeed drive="d" refreshKey={0} />);
    await waitFor(() => {
      expect(screen.getByTestId("activity-empty")).toBeInTheDocument();
    });
  });

  it("groups entries under Today / Yesterday headings", async () => {
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    mockListActivity.mockResolvedValue([
      entry({ file_id: "today1", created_at: today.toISOString() }),
      entry({ file_id: "yesterday1", created_at: yesterday.toISOString() }),
    ]);

    render(<ActivityFeed drive="d" refreshKey={0} />);

    await waitFor(() => {
      expect(screen.getByText("Today")).toBeInTheDocument();
      expect(screen.getByText("Yesterday")).toBeInTheDocument();
    });
  });

  it("renders subscription source badge with the subscription title", async () => {
    mockListActivity.mockResolvedValue([
      entry({
        file_id: "f1",
        source: "subscription",
        subscription_id: 7,
        subscription_title: "Fireship",
      }),
    ]);

    render(<ActivityFeed drive="d" refreshKey={0} />);

    await waitFor(() => {
      const badge = screen.getByTestId("source-badge-subscription");
      expect(badge).toHaveTextContent("Fireship");
    });
  });

  it("renders a 'Single import' badge for non-subscription entries", async () => {
    mockListActivity.mockResolvedValue([
      entry({ file_id: "f1", source: "single" }),
    ]);

    render(<ActivityFeed drive="d" refreshKey={0} />);

    await waitFor(() => {
      expect(screen.getByTestId("source-badge-single")).toBeInTheDocument();
    });
  });

  it("refetches when refreshKey changes", async () => {
    mockListActivity.mockResolvedValue([]);
    const { rerender } = render(
      <ActivityFeed drive="d" refreshKey={0} />,
    );
    await waitFor(() => {
      expect(mockListActivity).toHaveBeenCalledTimes(1);
    });

    rerender(<ActivityFeed drive="d" refreshKey={1} />);

    await waitFor(() => {
      expect(mockListActivity).toHaveBeenCalledTimes(2);
    });
  });
});
