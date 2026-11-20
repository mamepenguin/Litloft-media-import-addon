/**
 * SPEC-ADDON-162 / SPEC-ADDON-163: the Media Import page opened with
 * `?provider=&channel=` draws one channel's videos. The addon endpoint is
 * reached through `fetch`; the core `getShelves` is replaced. Both honour
 * their abort signal the way a real fetch does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import MediaImportPage from "@/addons/media_import/Page";
import { fileLinkHref } from "@/lib/canonicalFileUrl";
import enMessages from "@/messages/en.json";

const DRIVE = "d";
const ENDPOINT = "/api/addons/media_import/channel-videos";
const CHANNEL_QUERY = "provider=youtube&channel=Chan";

const nav = vi.hoisted(() => {
  const cache = new Map<string, URLSearchParams>();
  return {
    params: { name: "d" } as Record<string, string>,
    query: "",
    navigations: [] as string[],
    searchParams(query: string) {
      if (!cache.has(query)) cache.set(query, new URLSearchParams(query));
      return cache.get(query)!;
    },
  };
});

vi.mock("next/navigation", () => ({
  useParams: () => nav.params,
  useSearchParams: () => nav.searchParams(nav.query),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/drive/d/addons/media_import",
}));

// Next's Link navigates on a click its own onClick did not prevent; the
// record stands in for that client-side navigation.
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    onClick,
    ...props
  }: {
    children: ReactNode;
    href: string;
    onClick?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
    [key: string]: unknown;
  }) => (
    <a
      href={String(href)}
      data-next-link=""
      {...props}
      onClick={(e) => {
        onClick?.(e);
        if (!e.defaultPrevented) {
          e.preventDefault();
          nav.navigations.push(String(href));
        }
      }}
    >
      {children}
    </a>
  ),
}));

const getShelves = vi.fn();
const renameFile = vi.fn();
const moveFile = vi.fn();
const deleteFile = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getShelves: (...args: unknown[]) => getShelves(...args),
  renameFile: (...args: unknown[]) => renameFile(...args),
  moveFile: (...args: unknown[]) => moveFile(...args),
  deleteFile: (...args: unknown[]) => deleteFile(...args),
  getFolders: vi.fn().mockResolvedValue([]),
  getFolderTree: vi.fn().mockResolvedValue([]),
  getCollections: vi.fn().mockResolvedValue([]),
  createCollection: vi.fn(),
  addCollectionItems: vi.fn(),
}));

const listSubscriptions = vi.fn();

vi.mock("@/addons/media_import/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/addons/media_import/api")>()),
  listSubscriptions: (...args: unknown[]) => listSubscriptions(...args),
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
}));

vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: () => null }));

const clipboard = vi.hoisted(() => ({ copy: vi.fn(), cut: vi.fn() }));
vi.mock("@/components/ClipboardProvider", () => ({
  ClipboardProvider: ({ children }: { children: ReactNode }) => children,
  useClipboard: () => ({
    clipboard: null,
    copy: clipboard.copy,
    cut: clipboard.cut,
    paste: vi.fn(),
    clear: vi.fn(),
    isCut: () => false,
  }),
}));

const copyText = vi.hoisted(() => vi.fn());
vi.mock("@/lib/copyText", () => ({ copyText }));

vi.mock("@/components/RenameDialog", () => ({
  RenameDialog: ({ open, onRename }: { open: boolean; onRename: (name: string) => void }) =>
    open ? (
      <button type="button" onClick={() => onRename("Renamed.loft")}>
        fake-rename
      </button>
    ) : null,
}));
vi.mock("@/components/MoveDialog", () => ({
  MoveDialog: ({ open, onMove }: { open: boolean; onMove: (path: string) => void }) =>
    open ? (
      <button type="button" onClick={() => onMove("Elsewhere")}>
        fake-move
      </button>
    ) : null,
}));
vi.mock("@/components/ConfirmDialog", () => ({
  ConfirmDialog: ({ open, onConfirm }: { open: boolean; onConfirm: () => void }) =>
    open ? (
      <button type="button" onClick={onConfirm}>
        fake-confirm
      </button>
    ) : null,
}));
vi.mock("@/components/CollectionPicker", () => ({
  CollectionPicker: ({ open }: { open: boolean }) =>
    open ? <div data-testid="collection-picker" /> : null,
}));

const ADDON_ROOT = [
  resolve(process.cwd(), "../addons/media_import"),
  resolve(process.cwd(), "addons/media_import"),
].find((candidate) => existsSync(resolve(candidate, "frontend", "messages", "en.json")))!;

function addonMessages(locale: string) {
  return JSON.parse(
    readFileSync(resolve(ADDON_ROOT, "frontend", "messages", `${locale}.json`), "utf-8"),
  ) as { mediaImport: { channelView?: { empty?: unknown } } };
}

type MessageTree = { [key: string]: string | MessageTree };
function message(path: string): string {
  let node: unknown = enMessages as unknown as MessageTree;
  for (const part of path.split(".")) node = (node as MessageTree)[part];
  expect(typeof node).toBe("string");
  return node as string;
}

// --- helpers -----------------------------------------------------------------

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolveFn!: (value: T) => void;
  let rejectFn!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolveFn = res;
    rejectFn = rej;
  });
  return { promise, resolve: resolveFn, reject: rejectFn };
}

function abortable<T>(promise: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((res, rej) => {
    const abort = () => rej(new DOMException("The operation was aborted.", "AbortError"));
    if (signal.aborted) return abort();
    signal.addEventListener("abort", abort, { once: true });
    promise.then(res, rej);
  });
}

const titles = new Map<string, string>();
const dropped = new Set<string>();

function shelfItem(id: string) {
  return {
    id,
    filename: `${id}.loft`,
    title: titles.get(id) ?? `Video ${id}`,
    description: "",
    drive: DRIVE,
    folder_path: "",
    file_type: "video",
    mime_type: "application/vnd.litloft.loft+json",
    thumbnail_url: "",
    has_thumbnail: false,
    file_size: 1,
    duration: 120,
    image_width: null,
    image_height: null,
    liked_at: null,
    is_favorite: false,
    tags: [],
    subtitles: [],
    deleted_at: null,
    missing_since: null,
    trust_tier: "verified",
    trust_reviewed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

// Answers in reverse of the requested order, so card order has to come from the endpoint.
function shelvesFor(ids: readonly string[]) {
  const items = ids.filter((id) => !dropped.has(id)).map(shelfItem).reverse();
  return {
    by: "all",
    total_shelves: 1,
    base: "",
    shelves: [{ key: "all", label: "", count: items.length, items }],
  };
}

const ids = (n: number, prefix = "v") =>
  Array.from({ length: n }, (_, i) => `${prefix}${String(i).padStart(3, "0")}`);

type EndpointItem = { file_id: string; published_at?: string | null };

function answer(items: readonly (string | EndpointItem)[]): Response {
  const body = {
    items: items.map((it) =>
      typeof it === "string" ? { file_id: it, published_at: null } : { published_at: null, ...it },
    ),
  };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const endpoint = vi.fn<(url: URL, init?: RequestInit) => Promise<Response>>();
const endpointCalls: { url: URL; init?: RequestInit }[] = [];

const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const url = new URL(raw, "http://localhost");
  if (url.pathname === ENDPOINT) {
    endpointCalls.push({ url, init });
    return abortable(endpoint(url, init), init?.signal ?? undefined);
  }
  return new Response("{}", { status: 404 });
});

type ShelfCall = {
  drive: string;
  params: { ids?: readonly string[] } & Record<string, unknown>;
  signal?: AbortSignal;
  respond: () => void;
  fail: () => void;
};

/** getShelves answers only when the test says so. */
function manualShelves(): ShelfCall[] {
  const calls: ShelfCall[] = [];
  getShelves.mockImplementation(
    (drive: string, params: ShelfCall["params"], options?: { signal?: AbortSignal }) => {
      const d = deferred<ReturnType<typeof shelvesFor>>();
      calls.push({
        drive,
        params,
        signal: options?.signal,
        respond: () => d.resolve(shelvesFor(params.ids ?? [])),
        fail: () => d.reject(new Error("500")),
      });
      return abortable(d.promise, options?.signal);
    },
  );
  return calls;
}

const shelfIds = (n: number) => getShelves.mock.calls[n][1].ids as readonly string[];

const windowOpen = vi.fn();

class FakeObserver {
  static instances: FakeObserver[] = [];
  live = true;
  constructor(private callback: IntersectionObserverCallback) {
    FakeObserver.instances.push(this);
  }
  observe() {}
  disconnect() {
    this.live = false;
  }
  unobserve() {}
  takeRecords() {
    return [];
  }
  trigger() {
    this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as never);
  }
}

function nearEnd() {
  act(() => {
    for (const o of FakeObserver.instances.filter((o) => o.live)) o.trigger();
  });
}

async function settle(ms = 30) {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
}

function openChannel(query = CHANNEL_QUERY) {
  nav.query = query;
  return render(<MediaImportPage />);
}

const cardTitles = () => screen.queryAllByText(/^(Video|Renamed) /).map((el) => el.textContent);
const skeletons = (container: HTMLElement) => container.querySelectorAll("li.animate-pulse").length;
const cardOf = (id: string) => screen.getByText(titles.get(id) ?? `Video ${id}`).closest("a")!;
const retryButton = () => screen.queryByRole("button", { name: message("tree.errorRetry") });
const menuRows = () => screen.queryAllByRole("menuitem").map((el) => el.textContent?.trim());

beforeEach(() => {
  nav.params = { name: DRIVE };
  nav.query = "";
  nav.navigations = [];
  titles.clear();
  dropped.clear();
  endpointCalls.length = 0;
  FakeObserver.instances = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  vi.stubGlobal("fetch", fetchSpy);
  vi.stubGlobal("open", windowOpen);
  fetchSpy.mockClear();
  windowOpen.mockReset().mockReturnValue(null);
  endpoint.mockReset().mockImplementation(async () => answer(ids(3)));
  getShelves
    .mockReset()
    .mockImplementation(
      (_drive: string, params: { ids?: readonly string[] }, options?: { signal?: AbortSignal }) =>
        abortable(Promise.resolve(shelvesFor(params.ids ?? [])), options?.signal),
    );
  renameFile.mockReset().mockResolvedValue({});
  moveFile.mockReset().mockResolvedValue({});
  deleteFile.mockReset().mockResolvedValue(undefined);
  listSubscriptions.mockReset().mockResolvedValue([]);
  clipboard.copy.mockReset();
  clipboard.cut.mockReset();
  copyText.mockReset().mockResolvedValue(true);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// --- SPEC-ADDON-162: the page chooses the channel view ------------------------

describe("SPEC-ADDON-162 channel view header and page mode", () => {
  it("SPEC-ADDON-162 I8 draws the channel header at once and none of the management view", async () => {
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));
    const { container } = openChannel();

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("Chan");
    expect(heading.nextElementSibling?.textContent).toBe(DRIVE);
    await waitFor(() => expect(endpointCalls).toHaveLength(1));

    expect(container.querySelector("svg.lucide-rss")).toBeNull();
    expect(screen.queryByText("Loading drive...")).toBeNull();
    expect(screen.queryByTestId("composer-expanded")).toBeNull();
    expect(screen.queryByTestId("composer-collapsed")).toBeNull();
    expect(screen.queryAllByTestId(/^(dashboard-|subscription-card-)/)).toHaveLength(0);
    expect(screen.queryByTestId("activity-empty")).toBeNull();
    await settle();
    expect(listSubscriptions).not.toHaveBeenCalled();
  });

  it("SPEC-ADDON-162 item 10 puts a client-side back link to the management view before the title", async () => {
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));
    openChannel();

    const back = screen
      .getAllByRole("link")
      .find((a) => a.getAttribute("href") === `/drive/${DRIVE}/addons/media_import`)!;
    expect(back).toBeDefined();
    expect(back.textContent).toContain(message("mediaImport.sidebar.label"));
    const heading = screen.getByRole("heading", { level: 1 });
    expect(back.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(back);
    expect(nav.navigations).toEqual([`/drive/${DRIVE}/addons/media_import`]);
  });

  it("SPEC-ADDON-162 item 5 asks the endpoint with the raw query values and the drive header", async () => {
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));
    openChannel(
      `provider=${encodeURIComponent("you tube")}&channel=${encodeURIComponent(" Tom & Jerry / 公式 ")}`,
    );

    await waitFor(() => expect(endpointCalls).toHaveLength(1));
    const { url, init } = endpointCalls[0];
    expect(url.pathname).toBe(ENDPOINT);
    expect(url.searchParams.get("provider")).toBe("you tube");
    expect(url.searchParams.get("channel")).toBe(" Tom & Jerry / 公式 ");
    expect(new Headers(init?.headers).get("X-Lit-Drive")).toBe(DRIVE);
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it("SPEC-ADDON-162 §8 loading draws two rows of skeleton cards under the header", async () => {
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));
    const { container } = openChannel();

    await waitFor(() => expect(skeletons(container)).toBe(4));
    expect(retryButton()).toBeNull();
  });

  it("SPEC-ADDON-162 I6 an answer to the management view never replaces the channel view", async () => {
    const subs = deferred<unknown[]>();
    listSubscriptions.mockReturnValue(subs.promise);
    const view = render(<MediaImportPage />);
    await waitFor(() => expect(listSubscriptions).toHaveBeenCalled());

    nav.query = CHANNEL_QUERY;
    view.rerender(<MediaImportPage />);
    await screen.findByText("Video v000");
    await act(async () => subs.resolve([]));
    await settle();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chan");
    expect(screen.queryByTestId("composer-expanded")).toBeNull();
    expect(screen.queryByTestId("activity-empty")).toBeNull();
    expect(cardTitles()).toEqual(["Video v000", "Video v001", "Video v002"]);
  });

  it.each([
    ["provider only", "provider=youtube"],
    ["channel only", "channel=Chan"],
    ["an empty provider", "provider=&channel=Chan"],
    ["a blank provider", "provider=%20&channel=Chan"],
    ["a blank channel", "provider=youtube&channel=%20%09"],
  ])("SPEC-ADDON-162 I8 %s in the query draws the management view", async (_label, query) => {
    const view = openChannel();
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chan"),
    );

    nav.query = query;
    view.rerender(<MediaImportPage />);

    await waitFor(() => expect(screen.getByTestId("composer-expanded")).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      message("mediaImport.sidebar.label"),
    );
    expect(listSubscriptions.mock.calls.some((c) => c[0] === DRIVE)).toBe(true);
    expect(cardTitles()).toHaveLength(0);
  });

  it.each(["ja", "en"])("SPEC-ADDON-162 %s messages carry mediaImport.channelView.empty", (locale) => {
    const empty = addonMessages(locale).mediaImport.channelView?.empty;
    expect(typeof empty).toBe("string");
    expect((empty as string).trim()).not.toBe("");
  });
});

// --- SPEC-ADDON-162: cards ----------------------------------------------------

describe("SPEC-ADDON-162 cards", () => {
  it("SPEC-ADDON-162 I4 asks getShelves for 60 ids at a time and draws them in the endpoint's order", async () => {
    endpoint.mockImplementation(async () => answer(ids(130)));
    openChannel();

    await waitFor(() => expect(cardTitles()).toHaveLength(60));
    expect(getShelves).toHaveBeenCalledTimes(1);
    const [drive, params, options] = getShelves.mock.calls[0];
    expect(drive).toBe(DRIVE);
    expect(params).toMatchObject({ by: "all", key: "all", per_shelf: 60 });
    expect([...params.ids]).toEqual(ids(130).slice(0, 60));
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    expect(cardTitles()).toEqual(ids(60).map((id) => `Video ${id}`));
  });

  it("SPEC-ADDON-162 I5 reads further batches only near the end, and none once every id is read", async () => {
    endpoint.mockImplementation(async () => answer(ids(130)));
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(60));
    await settle();
    expect(getShelves).toHaveBeenCalledTimes(1);

    nearEnd();
    await waitFor(() => expect(cardTitles()).toHaveLength(120));
    expect([...shelfIds(1)]).toEqual(ids(130).slice(60, 120));

    nearEnd();
    await waitFor(() => expect(cardTitles()).toHaveLength(130));
    expect([...shelfIds(2)]).toEqual(ids(130).slice(120));
    expect(cardTitles()).toEqual(ids(130).map((id) => `Video ${id}`));

    nearEnd();
    await settle();
    expect(getShelves).toHaveBeenCalledTimes(3);
  });

  it.each([
    [1, 1],
    [60, 1],
    [61, 2],
  ])("SPEC-ADDON-162 I5 %i ids take %i getShelves calls in all", async (count, calls) => {
    endpoint.mockImplementation(async () => answer(ids(count)));
    openChannel();
    await waitFor(() => expect(cardTitles().length).toBeGreaterThan(0));

    for (let i = 0; i < 3; i++) {
      nearEnd();
      await settle();
    }

    expect(cardTitles()).toEqual(ids(count).map((id) => `Video ${id}`));
    expect(getShelves).toHaveBeenCalledTimes(calls);
  });

  it("SPEC-ADDON-162 item 7 skips an id the shelves do not return", async () => {
    dropped.add("v001");
    endpoint.mockImplementation(async () => answer(ids(4)));
    openChannel();

    await waitFor(() => expect(cardTitles()).toEqual(["Video v000", "Video v002", "Video v003"]));
  });

  it("SPEC-ADDON-162 I5 a batch that drew nothing fetches the next at once, without a scroll", async () => {
    for (const id of ids(60)) dropped.add(id);
    endpoint.mockImplementation(async () => answer(ids(130)));
    openChannel();

    await waitFor(() => expect(cardTitles()).toHaveLength(60));
    expect(cardTitles()[0]).toBe("Video v060");
    expect([...shelfIds(1)]).toEqual(ids(130).slice(60, 120));
    await settle();
    expect(getShelves).toHaveBeenCalledTimes(2);
  });

  it("SPEC-ADDON-162 I5 shows empty only after the last id resolved to nothing", async () => {
    const emptyText = addonMessages("en").mediaImport.channelView?.empty as string;
    expect(typeof emptyText).toBe("string");
    for (const id of ids(130)) dropped.add(id);
    endpoint.mockImplementation(async () => answer(ids(130)));
    const calls = manualShelves();
    const { container } = openChannel();

    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => calls[0].respond());
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(screen.queryByText(emptyText)).toBeNull();
    await act(async () => calls[1].respond());
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(screen.queryByText(emptyText)).toBeNull();
    await act(async () => calls[2].respond());

    expect(await screen.findByText(emptyText)).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-film")).not.toBeNull();
    expect(skeletons(container)).toBe(0);
    expect(retryButton()).toBeNull();
  });

  it("SPEC-ADDON-162 §8 no match draws the empty state without asking getShelves", async () => {
    const emptyText = addonMessages("en").mediaImport.channelView?.empty as string;
    expect(typeof emptyText).toBe("string");
    endpoint.mockImplementation(async () => answer([]));
    const { container } = openChannel();

    expect(await screen.findByText(emptyText)).toBeInTheDocument();
    expect(container.querySelector("svg.lucide-film")).not.toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chan");
    expect(retryButton()).toBeNull();

    nearEnd();
    await settle();
    expect(getShelves).not.toHaveBeenCalled();
    expect(endpointCalls).toHaveLength(1);
    expect(screen.getByText(emptyText)).toBeInTheDocument();
  });

  it("SPEC-ADDON-162 I5 runs one load step at a time and draws one skeleton row while it runs", async () => {
    endpoint.mockImplementation(async () => answer(ids(130)));
    const calls = manualShelves();
    const { container } = openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => calls[0].respond());
    await waitFor(() => expect(cardTitles()).toHaveLength(60));

    nearEnd();
    await waitFor(() => expect(calls).toHaveLength(2));
    nearEnd();
    nearEnd();
    await settle();

    expect(calls).toHaveLength(2);
    expect(cardTitles()).toHaveLength(60);
    expect(skeletons(container)).toBe(2);
  });

  it("SPEC-ADDON-162 I6 a later failed step keeps the cards, offers a retry, and the retry asks the same batch", async () => {
    endpoint.mockImplementation(async () => answer(ids(130)));
    const calls = manualShelves();
    const { container } = openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => calls[0].respond());
    await waitFor(() => expect(cardTitles()).toHaveLength(60));

    nearEnd();
    await waitFor(() => expect(calls).toHaveLength(2));
    await act(async () => calls[1].fail());

    const retry = await screen.findByRole("button", { name: message("tree.errorRetry") });
    expect(cardTitles()).toHaveLength(60);
    expect(skeletons(container)).toBe(0);

    nearEnd();
    await settle();
    expect(calls).toHaveLength(2);

    fireEvent.click(retry);
    await waitFor(() => expect(calls).toHaveLength(3));
    expect([...(calls[2].params.ids ?? [])]).toEqual(ids(130).slice(60, 120));
    await act(async () => calls[2].respond());

    await waitFor(() => expect(cardTitles()).toHaveLength(120));
    expect(retryButton()).toBeNull();
    expect(cardTitles()).toEqual(ids(120).map((id) => `Video ${id}`));
  });

  it("SPEC-ADDON-162 I9 the byline is the publication date alone", async () => {
    endpoint.mockImplementation(async () =>
      answer([
        { file_id: "v000", published_at: "20261101" },
        { file_id: "v001", published_at: null },
        { file_id: "v002", published_at: "not a date" },
      ]),
    );
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    const dated = cardOf("v000");
    expect(within(dated).getByText("Nov 1, 2026")).toBeInTheDocument();
    for (const id of ["v000", "v001", "v002"]) {
      expect(cardOf(id).textContent).not.toContain("Chan");
    }
    expect(cardOf("v001").textContent).not.toMatch(/20\d\d/);
    expect(cardOf("v002").textContent).not.toContain("not a date");
    expect(cardOf("v002").textContent).not.toMatch(/20\d\d/);
  });

  it("SPEC-ADDON-162 item 9 a card opens its file client-side", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    const card = cardOf("v001");
    expect(card.getAttribute("href")).toBe(fileLinkHref(shelfItem("v001")));
    fireEvent.click(screen.getByText("Video v001"));

    expect(nav.navigations).toEqual([fileLinkHref(shelfItem("v001"))]);
  });
});

// --- SPEC-ADDON-162: the first load failing ------------------------------------

describe("SPEC-ADDON-162 failed", () => {
  it.each([
    ["a 500 answer", () => Promise.resolve(new Response("{}", { status: 500 }))],
    [
      "a 404 answer",
      () =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "Drive not found: d" }), { status: 404 }),
        ),
    ],
    ["a 422 answer", () => Promise.resolve(new Response("{}", { status: 422 }))],
    ["a network failure", () => Promise.reject(new TypeError("Failed to fetch"))],
  ])(
    "SPEC-ADDON-162 I6 %s from the endpoint shows the retry alone, and the retry asks again from the first id",
    async (_label, fail) => {
      endpoint.mockImplementationOnce(fail).mockImplementation(async () => answer(ids(3)));
      const { container } = openChannel();

      const retry = await screen.findByRole("button", { name: message("tree.errorRetry") });
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chan");
      expect(cardTitles()).toHaveLength(0);
      expect(skeletons(container)).toBe(0);
      expect(getShelves).not.toHaveBeenCalled();

      nearEnd();
      await settle();
      expect(endpointCalls).toHaveLength(1);
      expect(getShelves).not.toHaveBeenCalled();

      fireEvent.click(retry);

      await waitFor(() => expect(cardTitles()).toEqual(["Video v000", "Video v001", "Video v002"]));
      expect(endpointCalls).toHaveLength(2);
      expect([...shelfIds(0)]).toEqual(ids(3));
      expect(retryButton()).toBeNull();
    },
  );

  it("SPEC-ADDON-162 I6 a failed getShelves call in the first load step is failed, and the retry calls the endpoint again", async () => {
    endpoint.mockImplementation(async () => answer(ids(3)));
    getShelves.mockRejectedValueOnce(new Error("500"));
    openChannel();

    const retry = await screen.findByRole("button", { name: message("tree.errorRetry") });
    expect(cardTitles()).toHaveLength(0);

    fireEvent.click(retry);

    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    expect(endpointCalls).toHaveLength(2);
    expect([...shelfIds(1)]).toEqual(ids(3));
  });

  it("SPEC-ADDON-162 I6 a failure in the second batch of the first load step is failed, not more-failed", async () => {
    for (const id of ids(60)) dropped.add(id);
    endpoint.mockImplementation(async () => answer(ids(130)));
    const calls = manualShelves();
    openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => calls[0].respond());
    await waitFor(() => expect(calls).toHaveLength(2));
    await act(async () => calls[1].fail());

    const retry = await screen.findByRole("button", { name: message("tree.errorRetry") });
    expect(cardTitles()).toHaveLength(0);

    fireEvent.click(retry);
    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect([...(calls[2].params.ids ?? [])]).toEqual(ids(60));
  });
});

// --- SPEC-ADDON-162 §3: aborts and stale answers --------------------------------

describe("SPEC-ADDON-162 aborts and stale answers", () => {
  it("SPEC-ADDON-162 I6 a channel change mounts a fresh view and drops the earlier channel's answer", async () => {
    const first = deferred<Response>();
    endpoint.mockImplementationOnce(() => first.promise).mockImplementation(async () =>
      answer(ids(2, "w")),
    );
    const view = openChannel();
    await waitFor(() => expect(endpointCalls).toHaveLength(1));

    nav.query = "provider=youtube&channel=Other";
    view.rerender(<MediaImportPage />);

    await waitFor(() => expect(cardTitles()).toEqual(["Video w000", "Video w001"]));
    expect(endpointCalls[0].init?.signal?.aborted).toBe(true);
    expect(endpointCalls[1].url.searchParams.get("channel")).toBe("Other");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Other");

    first.resolve(answer(ids(3)));
    await settle();
    expect(cardTitles()).toEqual(["Video w000", "Video w001"]);
  });

  it("SPEC-ADDON-162 I6 a provider change mounts a fresh view", async () => {
    const view = openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    endpoint.mockImplementation(async () => answer(ids(1, "w")));
    nav.query = "provider=vimeo&channel=Chan";
    view.rerender(<MediaImportPage />);

    await waitFor(() => expect(cardTitles()).toEqual(["Video w000"]));
    expect(endpointCalls.at(-1)!.url.searchParams.get("provider")).toBe("vimeo");
  });

  it("SPEC-ADDON-162 I6 a drive change mounts a fresh view on that drive", async () => {
    const view = openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    endpoint.mockImplementation(async () => answer(ids(1, "w")));
    nav.params = { name: "e" };
    view.rerender(<MediaImportPage />);

    await waitFor(() => expect(cardTitles()).toEqual(["Video w000"]));
    expect(new Headers(endpointCalls.at(-1)!.init?.headers).get("X-Lit-Drive")).toBe("e");
    expect(getShelves.mock.calls.at(-1)![0]).toBe("e");
  });

  it("SPEC-ADDON-162 §3 a channel change aborts a getShelves call in flight", async () => {
    endpoint.mockImplementation(async () => answer(ids(3)));
    const calls = manualShelves();
    const view = openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));

    nav.query = "provider=youtube&channel=Other";
    view.rerender(<MediaImportPage />);

    await waitFor(() => expect(calls[0].signal?.aborted).toBe(true));
  });

  it("SPEC-ADDON-162 §3 unmounting aborts the endpoint call in flight", async () => {
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));
    const view = openChannel();
    await waitFor(() => expect(endpointCalls).toHaveLength(1));

    view.unmount();

    expect(endpointCalls[0].init?.signal?.aborted).toBe(true);
  });

  it("SPEC-ADDON-162 §3 unmounting aborts a getShelves call in flight", async () => {
    const calls = manualShelves();
    const view = openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));

    view.unmount();

    expect(calls[0].signal?.aborted).toBe(true);
  });
});

// --- SPEC-ADDON-163: the card's context menu ------------------------------------

async function openMenuOn(id: string) {
  const title = screen.getByText(titles.get(id) ?? `Video ${id}`);
  const notPrevented = fireEvent.contextMenu(title, { clientX: 40, clientY: 50 });
  await waitFor(() => expect(screen.queryAllByRole("menuitem").length).toBeGreaterThan(0));
  return notPrevented;
}

async function chooseRow(label: string) {
  const row = screen.getAllByRole("menuitem").find((el) => el.textContent?.trim() === label)!;
  expect(row).toBeDefined();
  fireEvent.click(row);
}

describe("SPEC-ADDON-163 card context menu", () => {
  const ROWS = () => [
    message("file.openInNewTab"),
    message("common.download"),
    message("file.addToCollection"),
    message("clipboard.copy"),
    message("clipboard.cut"),
    message("file.copyId"),
    message("common.rename"),
    message("common.move"),
    message("trash.moveToTrash"),
  ];

  it("SPEC-ADDON-163 I10 right-click opens the file menu with the core rows in order, and not the browser's", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    const notPrevented = await openMenuOn("v001");

    expect(notPrevented).toBe(false);
    expect(menuRows()).toEqual(ROWS());
    expect(menuRows()).not.toContain(message("file.edit"));
    expect(nav.navigations).toEqual([]);
  });

  it("SPEC-ADDON-163 item 3 open in new tab opens the pressed card's file", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    await openMenuOn("v001");
    await chooseRow(message("file.openInNewTab"));

    await waitFor(() => expect(windowOpen).toHaveBeenCalledTimes(1));
    expect(windowOpen.mock.calls[0][0]).toBe(fileLinkHref(shelfItem("v001")));
  });

  it("SPEC-ADDON-163 I10 a 500 ms still press opens the menu, not before", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    vi.useFakeTimers();

    fireEvent.touchStart(screen.getByText("Video v001"), {
      touches: [{ clientX: 40, clientY: 50 }],
    });
    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(screen.queryAllByRole("menuitem")).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(1);
    });

    expect(menuRows()).toEqual(ROWS());
  });

  it("SPEC-ADDON-163 item 1 moving the finger cancels the press", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    vi.useFakeTimers();

    const title = screen.getByText("Video v001");
    fireEvent.touchStart(title, { touches: [{ clientX: 40, clientY: 50 }] });
    fireEvent.touchMove(title, { touches: [{ clientX: 40, clientY: 120 }] });
    act(() => {
      vi.advanceTimersByTime(800);
    });

    expect(screen.queryAllByRole("menuitem")).toHaveLength(0);
  });

  it("SPEC-ADDON-163 I10 a rename reloads the view from the endpoint and shows the new title", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    const reload = deferred<Response>();
    endpoint.mockImplementation(() => reload.promise);

    await openMenuOn("v001");
    await chooseRow(message("common.rename"));
    fireEvent.click(await screen.findByText("fake-rename"));

    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    expect(renameFile).toHaveBeenCalledWith("v001", "Renamed.loft");
    await waitFor(() => expect(cardTitles()).toHaveLength(0));

    titles.set("v001", "Renamed v001");
    await act(async () => reload.resolve(answer(ids(3))));

    await waitFor(() =>
      expect(cardTitles()).toEqual(["Video v000", "Renamed v001", "Video v002"]),
    );
  });

  it("SPEC-ADDON-163 item 4 a reload starts from loading", async () => {
    const { container } = openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    endpoint.mockImplementation(() => new Promise<Response>(() => {}));

    await openMenuOn("v001");
    await chooseRow(message("common.rename"));
    fireEvent.click(await screen.findByText("fake-rename"));

    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    await waitFor(() => expect(skeletons(container)).toBe(4));
    expect(cardTitles()).toHaveLength(0);
  });

  it("SPEC-ADDON-163 I10 a move reloads the view and the moved file stays", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    await openMenuOn("v002");
    await chooseRow(message("common.move"));
    fireEvent.click(await screen.findByText("fake-move"));

    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    expect(moveFile).toHaveBeenCalledWith("v002", "Elsewhere");
    await waitFor(() =>
      expect(cardTitles()).toEqual(["Video v000", "Video v001", "Video v002"]),
    );
  });

  it("SPEC-ADDON-163 I10 a trash reloads the view and the trashed file is gone", async () => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    endpoint.mockImplementation(async () => answer(["v000", "v002"]));

    await openMenuOn("v001");
    await chooseRow(message("trash.moveToTrash"));
    fireEvent.click(await screen.findByText("fake-confirm"));

    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    expect(deleteFile).toHaveBeenCalledWith("v001");
    await waitFor(() => expect(cardTitles()).toEqual(["Video v000", "Video v002"]));
  });

  it("SPEC-ADDON-163 item 4 a rename that fails does not reload", async () => {
    renameFile.mockRejectedValue(new Error("409"));
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    await openMenuOn("v001");
    await chooseRow(message("common.rename"));
    fireEvent.click(await screen.findByText("fake-rename"));

    await waitFor(() => expect(renameFile).toHaveBeenCalled());
    await settle();
    expect(endpointCalls).toHaveLength(1);
    expect(cardTitles()).toHaveLength(3);
  });

  it("SPEC-ADDON-163 §3 a menu reload aborts a getShelves call in flight and never draws its answer", async () => {
    endpoint.mockImplementation(async () => answer(ids(130)));
    const calls = manualShelves();
    openChannel();
    await waitFor(() => expect(calls).toHaveLength(1));
    await act(async () => calls[0].respond());
    await waitFor(() => expect(cardTitles()).toHaveLength(60));
    nearEnd();
    await waitFor(() => expect(calls).toHaveLength(2));

    endpoint.mockImplementation(async () => answer(ids(2)));
    await openMenuOn("v001");
    await chooseRow(message("common.rename"));
    fireEvent.click(await screen.findByText("fake-rename"));

    await waitFor(() => expect(endpointCalls).toHaveLength(2));
    expect(calls[1].signal?.aborted).toBe(true);
    await waitFor(() => expect(calls).toHaveLength(3));
    await act(async () => calls[2].respond());
    await waitFor(() => expect(cardTitles()).toEqual(["Video v000", "Video v001"]));
  });

  it.each([
    [
      "copy",
      () => message("clipboard.copy"),
      () => {
        expect(clipboard.copy).toHaveBeenCalledWith(["v001"], DRIVE, "");
      },
    ],
    [
      "cut",
      () => message("clipboard.cut"),
      () => {
        expect(clipboard.cut).toHaveBeenCalledWith(["v001"], DRIVE, "");
      },
    ],
    [
      "copy ID",
      () => message("file.copyId"),
      () => {
        expect(copyText).toHaveBeenCalledWith("v001");
      },
    ],
    [
      "download",
      () => message("common.download"),
      () => {
        expect(windowOpen).toHaveBeenCalledTimes(1);
      },
    ],
    [
      "open in new tab",
      () => message("file.openInNewTab"),
      () => {
        expect(windowOpen).toHaveBeenCalledTimes(1);
      },
    ],
    [
      "add to collection",
      () => message("file.addToCollection"),
      () => {
        expect(screen.getByTestId("collection-picker")).toBeInTheDocument();
      },
    ],
  ])("SPEC-ADDON-163 I10 %s does not reload the view", async (_label, row, effect) => {
    openChannel();
    await waitFor(() => expect(cardTitles()).toHaveLength(3));

    await openMenuOn("v001");
    await chooseRow(row());

    await waitFor(() => effect());
    await settle();
    expect(endpointCalls).toHaveLength(1);
    expect(cardTitles()).toHaveLength(3);
  });
});
