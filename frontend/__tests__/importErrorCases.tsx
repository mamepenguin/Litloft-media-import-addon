import { expect } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import Composer from "@/addons/media_import/Composer";
import ImportFromUrlMenuItem from "@/addons/media_import/ImportFromUrlMenuItem";
import { ShortcutsProvider } from "@/components/ShortcutsProvider";

export const LINK_PATH = "/api/addons/media_import/link";
export const IMPORTED_URL = "https://example.com/some/page";

export type LinkAnswer =
  | { kind: "response"; status: number; body: string; contentType?: string }
  | { kind: "network" };

function jsonAnswer(status: number, body: unknown): LinkAnswer {
  return { kind: "response", status, body: JSON.stringify(body), contentType: "application/json" };
}

/** Every answer of `POST /link` that is not one of the two named reasons. */
export const FALLBACK_ANSWERS: ReadonlyArray<{ id: string; answer: LinkAnswer; serverText: string }> = [
  { id: "500 failed", answer: jsonAnswer(500, { detail: "failed" }), serverText: "failed" },
  { id: "422 URL is required", answer: jsonAnswer(422, { detail: "URL is required" }), serverText: "URL is required" },
  {
    id: "400 drive mismatch",
    answer: jsonAnswer(400, { detail: "X-Lit-Drive does not match the drive" }),
    serverText: "X-Lit-Drive does not match the drive",
  },
  { id: "404 Drive not found", answer: jsonAnswer(404, { detail: "Drive not found" }), serverText: "Drive not found" },
  {
    id: "422 validation list",
    answer: jsonAnswer(422, { detail: [{ loc: ["body", "url"], msg: "Field required", type: "missing" }] }),
    serverText: "Field required",
  },
  {
    id: "500 not JSON",
    answer: { kind: "response", status: 500, body: "Internal Server Error", contentType: "text/plain" },
    serverText: "Internal Server Error",
  },
  {
    id: "200 not JSON",
    answer: { kind: "response", status: 200, body: "<html>proxy</html>", contentType: "text/html" },
    serverText: "proxy",
  },
  { id: "network error", answer: { kind: "network" }, serverText: "Failed to fetch" },
];

export const NAMED_ANSWERS = {
  unsupported_url: jsonAnswer(422, { detail: "unsupported_url" }),
  unavailable: jsonAnswer(422, { detail: "unavailable" }),
} as const;

export function linkResponse(answer: LinkAnswer): Promise<Response> {
  if (answer.kind === "network") return Promise.reject(new TypeError("Failed to fetch"));
  return Promise.resolve(
    new Response(answer.body, {
      status: answer.status,
      headers: { "content-type": answer.contentType ?? "application/json" },
    }),
  );
}

function isLinkPost(input: RequestInfo | URL, init?: RequestInit): boolean {
  const url = String(input instanceof Request ? input.url : input);
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  return method === "POST" && new URL(url, "http://localhost").pathname === LINK_PATH;
}

/**
 * A fetch that answers `POST /link` with `answer` and the policy and addon
 * catalogue reads with an enabled Media Import; anything else is a 404.
 */
export function stubFetch(answer: () => LinkAnswer) {
  const linkCalls: unknown[] = [];
  const spy = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (isLinkPost(input, init)) {
      linkCalls.push(init?.body ?? null);
      return linkResponse(answer());
    }
    const url = String(input);
    const json = (data: unknown) =>
      new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
    if (url === "/api/drives/d/addon-policies") {
      return json({ addons: { media_import: { default: true, features: { url_import: true } } } });
    }
    if (url.startsWith("/api/addons/status")) {
      return json({ addons: { media_import: { label: "Media Import", icon: "link", scope: "drive" } }, slots: {} });
    }
    return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
  };
  return { spy, linkCalls };
}

export interface ImportForm {
  name: string;
  /** Renders the form, submits IMPORTED_URL and resolves once POST /link was sent. */
  submit: (linkCalls: unknown[]) => Promise<void>;
  urlField: () => HTMLElement;
}

export function composerForm(placeholder = "https://..."): ImportForm {
  return {
    name: "Composer",
    async submit(linkCalls) {
      render(<Composer drive="d" initialExpanded={true} onCreated={() => {}} />);
      fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value: IMPORTED_URL } });
      await waitFor(() => expect(screen.getByTestId("composer-url-hint")).toBeInTheDocument());
      fireEvent.click(screen.getByTestId("composer-submit"));
      await waitFor(() => expect(linkCalls).toHaveLength(1));
    },
    urlField: () => screen.getByPlaceholderText(placeholder),
  };
}

export function dialogForm(labels: { row: string; url: string; submit: string }): ImportForm {
  return {
    name: "dialog",
    async submit(linkCalls) {
      render(
        <ShortcutsProvider>
          <ImportFromUrlMenuItem drive="d" path="" onRequestClose={() => {}} onDialogOpenChange={() => {}} />
        </ShortcutsProvider>,
      );
      fireEvent.click(screen.getByRole("menuitem", { name: labels.row }));
      fireEvent.change(screen.getByLabelText(labels.url), { target: { value: IMPORTED_URL } });
      fireEvent.click(screen.getByRole("button", { name: labels.submit }));
      await waitFor(() => expect(linkCalls).toHaveLength(1));
    },
    urlField: () => screen.getByLabelText(labels.url),
  };
}
