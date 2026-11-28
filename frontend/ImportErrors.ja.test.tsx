/**
 * SPEC-ADDON-188 I2 / I2b in Japanese. The shared test setup renders English,
 * so this file resolves keys against the merged ja tree.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";

import {
  FALLBACK_ANSWERS,
  IMPORTED_URL,
  type ImportForm,
  type LinkAnswer,
  NAMED_ANSWERS,
  composerForm,
  dialogForm,
  stubFetch,
} from "@/addons/media_import/__tests__/importErrorCases";
import { _resetMemoryForTests } from "@/addons/media_import/lib/smartFolderMemory";
import { _resetPolicyCache } from "@/hooks/usePolicy";
import { invalidateAddonsCache } from "@/lib/addons";

vi.mock("next-intl", async () => {
  const ja = (await import("@/messages/ja.json")).default as Record<string, unknown>;
  const lookup = (path: string): unknown =>
    path.split(".").reduce<unknown>(
      (node, part) =>
        typeof node === "object" && node !== null
          ? (node as Record<string, unknown>)[part]
          : undefined,
      ja,
    );
  const cache = new Map<string, (key: string, values?: Record<string, unknown>) => string>();
  const useTranslations = (namespace?: string) => {
    const ns = namespace ?? "";
    if (!cache.has(ns)) {
      cache.set(ns, (key, values) => {
        const path = namespace === undefined ? key : `${namespace}.${key}`;
        const raw = lookup(path);
        let text = typeof raw === "string" ? raw : path;
        for (const [k, v] of Object.entries(values ?? {})) {
          text = text.replace(`{${k}}`, String(v));
        }
        return text;
      });
    }
    return cache.get(ns)!;
  };
  return {
    useTranslations,
    useLocale: () => "ja",
    NextIntlClientProvider: ({ children }: { children: React.ReactNode }) => children,
  };
});

const mockResolveUrl = vi.fn();

vi.mock("@/addons/media_import/api", async () => {
  const actual = await vi.importActual<typeof import("@/addons/media_import/api")>(
    "@/addons/media_import/api",
  );
  return {
    ...actual,
    resolveSubscriptionUrl: (...args: unknown[]) => mockResolveUrl(...args),
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getFolders: vi.fn().mockResolvedValue([]),
    getFolderTree: vi.fn().mockResolvedValue([]),
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

const UNSUPPORTED = "このURLからは動画を読み取れません。対応していないサイトか、動画のないページです";
const UNAVAILABLE = "この動画は取り込めません。非公開・削除済み・地域制限の可能性があります";
const FALLBACK = "取り込みに失敗しました";

const FORMS: ReadonlyArray<ImportForm> = [
  composerForm(),
  dialogForm({ row: "URL から取り込む", url: "動画の URL", submit: "取り込む" }),
];

let answer: LinkAnswer;
let linkCalls: unknown[];

beforeEach(() => {
  vi.clearAllMocks();
  _resetPolicyCache();
  invalidateAddonsCache();
  window.localStorage.clear();
  mockResolveUrl.mockResolvedValue({ kind: "unknown", provider: null, ref: null });
  const stub = stubFetch(() => answer);
  linkCalls = stub.linkCalls;
  vi.stubGlobal("fetch", vi.fn(stub.spy));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  _resetMemoryForTests();
  _resetPolicyCache();
  invalidateAddonsCache();
});

function messageArea(): string {
  return (document.body.textContent ?? "").replace(IMPORTED_URL, "");
}

describe.each(FORMS)("SPEC-ADDON-188 $name in Japanese", (form) => {
  it.each([
    { code: "unsupported_url" as const, message: UNSUPPORTED },
    { code: "unavailable" as const, message: UNAVAILABLE },
  ])("SPEC-ADDON-188 I2: $code shows the Japanese reason and no English", async ({ code, message }) => {
    answer = NAMED_ANSWERS[code];

    await form.submit(linkCalls);

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(messageArea()).not.toContain("Failed to create link");
    expect(messageArea()).not.toContain(code);
  });

  it.each(FALLBACK_ANSWERS)("SPEC-ADDON-188 I2b: $id shows the Japanese fallback", async ({ answer: given, serverText }) => {
    answer = given;

    await form.submit(linkCalls);

    expect(await screen.findByText(FALLBACK)).toBeInTheDocument();
    expect(messageArea()).not.toContain(serverText);
  });
});
