import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ImportFromUrlMenuItem from "@/addons/media_import/ImportFromUrlMenuItem";
import { rememberFolder, _resetMemoryForTests } from "@/addons/media_import/lib/smartFolderMemory";
import { STT_MODE_STORAGE_KEY } from "@/addons/media_import/lib/sttMode";
import { AddonSlot } from "@/components/AddonSlot";
import { AddonSlotsProvider } from "@/components/AddonSlotsProvider";
import { AddButton } from "@/components/AddButton";
import { ShortcutsProvider } from "@/components/ShortcutsProvider";
import { _resetPolicyCache } from "@/hooks/usePolicy";
import { invalidateAddonsCache } from "@/lib/addons";
import { COMPOSITION_GRACE_MS } from "@/lib/ime";

const mockResolveUrl = vi.fn();
const mockCreateLoft = vi.fn();
const mockCreateSubscription = vi.fn();
const mockSyncSubscription = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    resolveSubscriptionUrl: (...args: unknown[]) => mockResolveUrl(...args),
    createLoft: (...args: unknown[]) => mockCreateLoft(...args),
    createSubscription: (...args: unknown[]) => mockCreateSubscription(...args),
    syncSubscription: (...args: unknown[]) => mockSyncSubscription(...args),
  };
});

vi.mock("@/components/FolderPicker", () => ({
  FolderPicker: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input aria-label="folder" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

vi.mock("@/components/CurrentDriveProvider", () => ({
  useCurrentDrive: () => "d",
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

type PolicyAnswer = "enabled" | "disabled" | "error";
let policyAnswer: PolicyAnswer;
let policyGate: Promise<void> | null = null;
let catalogue: { addons: Record<string, unknown>; slots: Record<string, unknown> };

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const fetchSpy = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/drives/d/addon-policies") {
    if (policyGate) await policyGate;
    if (policyAnswer === "error") return json({}, 500);
    return json({
      addons: {
        media_import: {
          default: true,
          features: { url_import: policyAnswer === "enabled" },
        },
      },
    });
  }
  if (url === "/api/addons/status?drive=d") return json(catalogue);
  return json({}, 404);
});

const ROW = "Import from URL";
const VIDEO = "https://www.youtube.com/watch?v=abc";

const MEDIA_IMPORT_CATALOGUE = {
  addons: { media_import: { label: "Media Import", icon: "link", scope: "drive" } },
  slots: {
    "folder-actions-menu": [
      { id: "media-import-url", label: "Import from URL", priority: 20, addonName: "media_import" },
    ],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("fetch", fetchSpy);
  _resetPolicyCache();
  invalidateAddonsCache();
  window.localStorage.clear();
  policyAnswer = "enabled";
  policyGate = null;
  catalogue = MEDIA_IMPORT_CATALOGUE;
  mockResolveUrl.mockResolvedValue({ kind: "video", provider: "youtube", ref: "abc" });
  mockCreateLoft.mockResolvedValue({ file_id: "f1", filename: "x.loft" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  _resetMemoryForTests();
  _resetPolicyCache();
  invalidateAddonsCache();
});

function storageSnapshot(): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i)!;
    out[key] = window.localStorage.getItem(key);
  }
  return out;
}

function settle() {
  return act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function policyLoaded() {
  return waitFor(() =>
    expect(fetchSpy.mock.calls.map((c) => String(c[0]))).toContain("/api/drives/d/addon-policies"),
  );
}

function renderRow(props: Record<string, unknown> = {}) {
  const onRequestClose = vi.fn();
  const onDialogOpenChange = vi.fn();
  render(
    <ShortcutsProvider>
      <ImportFromUrlMenuItem
        drive="d"
        path=""
        onRequestClose={onRequestClose}
        onDialogOpenChange={onDialogOpenChange}
        {...props}
      />
    </ShortcutsProvider>,
  );
  return { onRequestClose, onDialogOpenChange };
}

function openDialog() {
  fireEvent.click(screen.getByRole("menuitem", { name: ROW }));
  return screen.getByRole("dialog", { name: ROW });
}

function submitUrl(url: string) {
  fireEvent.change(screen.getByLabelText("Video URL"), { target: { value: url } });
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
}

describe("Import from URL row and policy", () => {
  it("is drawn while the policy is still loading", () => {
    renderRow();
    expect(screen.getByRole("menuitem", { name: ROW })).toBeInTheDocument();
  });

  it("disappears once url_import is off for the drive", async () => {
    policyAnswer = "disabled";
    renderRow();
    await policyLoaded();
    await waitFor(() =>
      expect(screen.queryByRole("menuitem", { name: ROW })).not.toBeInTheDocument(),
    );
  });

  it.each<PolicyAnswer>(["enabled", "error"])("stays after the policy answers %s", async (answer) => {
    policyAnswer = answer;
    renderRow();
    await policyLoaded();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.getByRole("menuitem", { name: ROW })).toBeInTheDocument();
  });
});

describe("Import from URL row and the drive catalogue", () => {
  function renderSlot() {
    render(
      <ShortcutsProvider>
        <AddonSlotsProvider>
          <div role="menu">
            <AddonSlot id="folder-actions-menu" layout="stack" props={{ drive: "d", path: "" }} />
          </div>
        </AddonSlotsProvider>
      </ShortcutsProvider>,
    );
  }

  it("is drawn through the slot when the catalogue carries it", async () => {
    renderSlot();
    expect(await screen.findByRole("menuitem", { name: ROW })).toBeInTheDocument();
  });

  it("is not drawn when the catalogue drops the addon for the drive", async () => {
    catalogue = { addons: {}, slots: {} };
    renderSlot();
    await waitFor(() =>
      expect(fetchSpy.mock.calls.map((c) => String(c[0]))).toContain("/api/addons/status?drive=d"),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByRole("menuitem", { name: ROW })).not.toBeInTheDocument();
  });
});

describe("Import from URL dialog", () => {
  it("starts at the drive root for path '' even with a remembered folder, and leaves the memory alone", async () => {
    rememberFolder("d", "youtube", "video", "remembered");
    const before = storageSnapshot();
    expect(before).not.toEqual({});
    renderRow({ path: "" });
    openDialog();
    expect(screen.getByLabelText("folder")).toHaveValue("");

    submitUrl(VIDEO);

    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockCreateLoft.mock.calls[0].slice(0, 3)).toEqual([VIDEO, "d", ""]);
    expect(storageSnapshot()).toEqual(before);
  });

  it("starts at the Add menu's folder and sends the folder the user picked", async () => {
    rememberFolder("d", "youtube", "video", "remembered");
    const before = storageSnapshot();
    renderRow({ path: "videos/yt" });
    openDialog();
    expect(screen.getByLabelText("folder")).toHaveValue("videos/yt");
    fireEvent.change(screen.getByLabelText("folder"), { target: { value: "videos/other" } });

    submitUrl(VIDEO);

    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockCreateLoft.mock.calls[0].slice(0, 3)).toEqual([VIDEO, "d", "videos/other"]);
    expect(storageSnapshot()).toEqual(before);
  });

  it("passes the stored speech-to-text mode", async () => {
    window.localStorage.setItem(STT_MODE_STORAGE_KEY, "always");
    renderRow();
    openDialog();
    submitUrl(VIDEO);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockCreateLoft.mock.calls[0][3]).toBe("always");
  });

  it("closes the dialog and the menu once, after a successful import", async () => {
    const { onRequestClose, onDialogOpenChange } = renderRow();
    openDialog();
    submitUrl(VIDEO);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(mockCreateLoft).toHaveBeenCalledTimes(1);
    expect(onRequestClose).toHaveBeenCalledTimes(1);
    expect(onDialogOpenChange.mock.calls).toEqual([[true], [false]]);
    expect(mockCreateSubscription).not.toHaveBeenCalled();
    expect(mockSyncSubscription).not.toHaveBeenCalled();
  });

  it.each(["channel", "playlist", "feed"])(
    "sends nothing for a %s URL and keeps the dialog and its input",
    async (kind) => {
      mockResolveUrl.mockResolvedValue({ kind, provider: "youtube", ref: "r" });
      rememberFolder("d", "youtube", kind as "channel", "remembered");
      const before = storageSnapshot();
      const { onRequestClose } = renderRow({ path: "videos" });
      openDialog();
      fireEvent.change(screen.getByLabelText("folder"), { target: { value: "videos/sub" } });
      submitUrl("https://www.youtube.com/@someone");

      const notice = await screen.findByText(/Subscribe to it from the Media Import page/);
      expect(notice).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open Media Import" })).toHaveAttribute(
        "href",
        "/drive/d/addons/media_import",
      );
      expect(mockCreateLoft).not.toHaveBeenCalled();
      expect(mockCreateSubscription).not.toHaveBeenCalled();
      expect(mockSyncSubscription).not.toHaveBeenCalled();
      expect(screen.getByRole("dialog", { name: ROW })).toBeInTheDocument();
      expect(screen.getByLabelText("Video URL")).toHaveValue("https://www.youtube.com/@someone");
      expect(screen.getByLabelText("folder")).toHaveValue("videos/sub");
      expect(onRequestClose).not.toHaveBeenCalled();
      expect(storageSnapshot()).toEqual(before);
    },
  );

  it("keeps the dialog, the URL and the folder when the import fails", async () => {
    mockCreateLoft.mockRejectedValue(new Error("Failed to create link"));
    rememberFolder("d", "youtube", "video", "remembered");
    const before = storageSnapshot();
    const { onRequestClose, onDialogOpenChange } = renderRow({ path: "videos" });
    openDialog();
    fireEvent.change(screen.getByLabelText("folder"), { target: { value: "videos/sub" } });
    submitUrl(VIDEO);

    expect(await screen.findByText("Failed to create link")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: ROW })).toBeInTheDocument();
    expect(screen.getByLabelText("Video URL")).toHaveValue(VIDEO);
    expect(screen.getByLabelText("folder")).toHaveValue("videos/sub");
    expect(onRequestClose).not.toHaveBeenCalled();
    expect(onDialogOpenChange.mock.calls).toEqual([[true]]);
    expect(storageSnapshot()).toEqual(before);
  });

  it("returns to the menu on cancel without asking it to close", () => {
    const { onRequestClose, onDialogOpenChange } = renderRow();
    openDialog();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onRequestClose).not.toHaveBeenCalled();
    expect(onDialogOpenChange.mock.calls).toEqual([[true], [false]]);
  });

  it("sends the same request whatever surface or fileIds the host adds", async () => {
    renderRow({ path: "videos", surface: "home", fileIds: ["a", "b"] });
    openDialog();
    submitUrl(VIDEO);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    cleanup();
    renderRow({ path: "videos" });
    openDialog();
    submitUrl(VIDEO);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(2));
    expect(mockCreateLoft.mock.calls[0]).toEqual(mockCreateLoft.mock.calls[1]);
  });
});

describe("Import from URL dialog inside the Add menu", () => {
  async function openFromAdd() {
    render(
      <ShortcutsProvider>
        <AddonSlotsProvider>
          <AddButton addonProps={{ drive: "d", path: "videos" }} />
        </AddonSlotsProvider>
      </ShortcutsProvider>,
    );
    const trigger = screen.getByRole("button", { name: /Add/ });
    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole("menuitem", { name: ROW }));
    return { trigger, dialog: screen.getByRole("dialog", { name: ROW }) };
  }

  it("keeps the dialog and what was typed when it is pressed and typed into", async () => {
    await openFromAdd();
    const field = screen.getByLabelText("Video URL");
    fireEvent.pointerDown(field);
    fireEvent.change(field, { target: { value: VIDEO } });
    fireEvent.click(field);
    fireEvent.pointerDown(screen.getByLabelText("folder"));
    fireEvent.change(screen.getByLabelText("folder"), { target: { value: "videos/new" } });

    expect(screen.getByRole("dialog", { name: ROW })).toBeInTheDocument();
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByLabelText("Video URL")).toHaveValue(VIDEO);
    expect(screen.getByLabelText("folder")).toHaveValue("videos/new");
  });

  it("closes only the dialog on the first Escape and the menu on the second", async () => {
    const { trigger } = await openFromAdd();
    const field = screen.getByLabelText("Video URL");
    fireEvent.keyDown(field, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

describe("Import from URL dialog when the policy settles after it opened", () => {
  it("keeps the dialog and its input, and still reports its close", async () => {
    let release!: () => void;
    policyGate = new Promise<void>((r) => {
      release = r;
    });
    policyAnswer = "disabled";
    const { onDialogOpenChange } = renderRow({ path: "videos" });
    openDialog();
    fireEvent.change(screen.getByLabelText("Video URL"), { target: { value: VIDEO } });

    await act(async () => {
      release();
    });
    await waitFor(() =>
      expect(screen.queryByRole("menuitem", { name: ROW })).not.toBeInTheDocument(),
    );

    expect(screen.getByRole("dialog", { name: ROW })).toBeInTheDocument();
    expect(screen.getByLabelText("Video URL")).toHaveValue(VIDEO);
    expect(screen.getByLabelText("folder")).toHaveValue("videos");

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onDialogOpenChange.mock.calls).toEqual([[true], [false]]);
  });
});

describe("Import from URL dialog submitting", () => {
  it("classifies the URL against the Add menu's drive", async () => {
    renderRow({ path: "videos" });
    openDialog();
    submitUrl(VIDEO);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockResolveUrl.mock.calls).toEqual([[VIDEO, "d"]]);
  });

  it("sends one import however many times Enter is pressed while it is pending", async () => {
    let release!: (v: unknown) => void;
    mockResolveUrl.mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    renderRow({ path: "videos" });
    openDialog();
    const field = screen.getByLabelText("Video URL");
    fireEvent.change(field, { target: { value: VIDEO } });
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.keyDown(field, { key: "Enter" });
    await act(async () => {
      release({ kind: "video", provider: "youtube", ref: "abc" });
    });
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    await settle();
    expect(mockResolveUrl).toHaveBeenCalledTimes(1);
    expect(mockCreateLoft).toHaveBeenCalledTimes(1);
  });

  it("can import again after a failed import", async () => {
    mockCreateLoft.mockRejectedValueOnce(new Error("Failed to create link"));
    renderRow({ path: "videos" });
    openDialog();
    submitUrl(VIDEO);
    await screen.findByText("Failed to create link");

    const button = screen.getByRole("button", { name: "Import" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("can import again after the subscription notice", async () => {
    mockResolveUrl.mockResolvedValueOnce({ kind: "channel", provider: "youtube", ref: "r" });
    renderRow({ path: "videos" });
    openDialog();
    submitUrl("https://www.youtube.com/@someone");
    await screen.findByText(/Subscribe to it from the Media Import page/);

    submitUrl(VIDEO);
    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    expect(mockCreateLoft.mock.calls[0].slice(0, 3)).toEqual([VIDEO, "d", "videos"]);
  });
});

describe("Import from URL dialog importing after its row was hidden", () => {
  it("closes the menu and reports the dialog closed", async () => {
    let release!: () => void;
    policyGate = new Promise<void>((r) => {
      release = r;
    });
    policyAnswer = "disabled";
    const { onRequestClose, onDialogOpenChange } = renderRow({ path: "videos" });
    openDialog();
    await act(async () => {
      release();
    });
    await waitFor(() =>
      expect(screen.queryByRole("menuitem", { name: ROW })).not.toBeInTheDocument(),
    );

    submitUrl(VIDEO);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onRequestClose).toHaveBeenCalledTimes(1);
    expect(onDialogOpenChange.mock.calls).toEqual([[true], [false]]);
  });
});

describe("Import from URL dialog IME composition", () => {
  let now: ReturnType<typeof vi.spyOn> | null = null;

  afterEach(() => {
    now?.mockRestore();
    now = null;
  });

  function openWithConvertedUrl() {
    renderRow({ path: "videos" });
    openDialog();
    const field = screen.getByLabelText("Video URL");
    fireEvent.compositionStart(field);
    fireEvent.change(field, { target: { value: VIDEO } });
    fireEvent.compositionEnd(field, { data: VIDEO });
    return field;
  }

  it("does not import on the Enter that confirms a conversion", async () => {
    now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const field = openWithConvertedUrl();
    now.mockReturnValue(1_000_000 + COMPOSITION_GRACE_MS - 1);

    fireEvent.keyDown(field, { key: "Enter", keyCode: 13 });
    await settle();

    expect(mockResolveUrl).not.toHaveBeenCalled();
    expect(mockCreateLoft).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: ROW })).toBeInTheDocument();
  });

  it("does not import on an Enter the IME still owns", async () => {
    const field = openWithConvertedUrl();
    fireEvent.compositionStart(field);

    fireEvent.keyDown(field, { key: "Enter", isComposing: true });
    fireEvent.keyDown(field, { key: "Enter", keyCode: 229 });
    await settle();

    expect(mockResolveUrl).not.toHaveBeenCalled();
    expect(mockCreateLoft).not.toHaveBeenCalled();
  });

  it("imports once on an Enter pressed after the grace window", async () => {
    now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    const field = openWithConvertedUrl();
    now.mockReturnValue(1_000_000 + COMPOSITION_GRACE_MS);

    fireEvent.keyDown(field, { key: "Enter", keyCode: 13 });

    await waitFor(() => expect(mockCreateLoft).toHaveBeenCalledTimes(1));
    await settle();
    expect(mockResolveUrl.mock.calls).toEqual([[VIDEO, "d"]]);
    expect(mockCreateLoft).toHaveBeenCalledTimes(1);
  });
});
