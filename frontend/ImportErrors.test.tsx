/**
 * SPEC-ADDON-188: Import from URL names why `POST /link` refused, in the
 * Composer and the folder-menu dialog. `createLoft` is the real one; only
 * the server's answer is replaced.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

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

const UNSUPPORTED =
  "No video could be read from this URL. The site is not supported, or the page has no video.";
const UNAVAILABLE = "This video cannot be imported. It may be private, removed or region-locked.";

const FORMS: ReadonlyArray<{ form: ImportForm; fallback: string }> = [
  { form: composerForm(), fallback: "Failed to import" },
  {
    form: dialogForm({ row: "Import from URL", url: "Video URL", submit: "Import" }),
    fallback: "Import failed",
  },
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

function assertNoServerText(serverText: string, fallback: string) {
  const shown = (document.body.textContent ?? "").replaceAll(fallback, "");
  for (const text of [serverText, "unsupported_url", "unavailable", "Failed to create link"]) {
    expect(shown).not.toContain(text);
  }
}

describe.each(FORMS)("SPEC-ADDON-188 $form.name names why the import was refused", ({ form, fallback }) => {
  it.each([
    { code: "unsupported_url" as const, message: UNSUPPORTED },
    { code: "unavailable" as const, message: UNAVAILABLE },
  ])("SPEC-ADDON-188 I2: $code shows its own message and keeps the URL", async ({ code, message }) => {
    answer = NAMED_ANSWERS[code];

    await form.submit(linkCalls);

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(fallback)).not.toBeInTheDocument();
    expect(form.urlField()).toHaveValue(IMPORTED_URL);
    expect(document.body.textContent).not.toContain(code);
  });

  it.each(FALLBACK_ANSWERS)(
    "SPEC-ADDON-188 I2b: $id shows the form's fallback, never the server's text",
    async ({ answer: given, serverText }) => {
      answer = given;

      await form.submit(linkCalls);

      expect(await screen.findByText(fallback)).toBeInTheDocument();
      assertNoServerText(serverText, fallback);
      expect(screen.queryByText(UNSUPPORTED)).not.toBeInTheDocument();
      expect(screen.queryByText(UNAVAILABLE)).not.toBeInTheDocument();
      expect(form.urlField()).toHaveValue(IMPORTED_URL);
    },
  );

  it("SPEC-ADDON-188 §3: the message stays when the URL is edited and changes only on the next submit", async () => {
    answer = NAMED_ANSWERS.unsupported_url;
    await form.submit(linkCalls);
    expect(await screen.findByText(UNSUPPORTED)).toBeInTheDocument();

    fireEvent.change(form.urlField(), { target: { value: "https://example.com/other" } });
    expect(screen.getByText(UNSUPPORTED)).toBeInTheDocument();

    answer = NAMED_ANSWERS.unavailable;
    const submit = form.name === "Composer"
      ? screen.getByTestId("composer-submit")
      : screen.getByRole("button", { name: "Import" });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);
    await waitFor(() => expect(linkCalls).toHaveLength(2));

    expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText(UNSUPPORTED)).not.toBeInTheDocument();
  });
});
