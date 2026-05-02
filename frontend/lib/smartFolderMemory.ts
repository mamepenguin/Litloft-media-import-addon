/**
 * localStorage-backed memory of the most recently used folder per
 * (drive, provider, kind) tuple — so the second time a user pastes
 * a YouTube channel URL, the destination folder is already filled
 * in with whatever they chose last time.
 *
 * Drive is part of the key because folder paths only make sense
 * within one drive (drive boundary is a security concept, see hako
 * cRNeIvcbhz449BwTmof5m). Provider+kind together capture intent
 * better than either alone — "YouTube channel" usually goes to a
 * different folder from "YouTube playlist".
 *
 * SSR-safe: ``typeof window`` guard skips the localStorage call in
 * Next.js server components. All helpers tolerate quota / parse
 * errors silently — the feature degrades to "no memory" rather
 * than blocking the user.
 */

const STORAGE_KEY = "media_import.last_folder_v1";

interface MemoryShape {
  // key: ``${drive}|${provider}|${kind}`` ; value: folder path
  [key: string]: string;
}

function _key(drive: string, provider: string, kind: string): string {
  return `${drive}|${provider}|${kind}`;
}

function _read(): MemoryShape {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function _write(state: MemoryShape): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota exceeded or storage disabled — silently degrade.
  }
}

export function getLastFolder(
  drive: string,
  provider: string,
  kind: string,
): string | null {
  const memory = _read();
  const folder = memory[_key(drive, provider, kind)];
  return typeof folder === "string" ? folder : null;
}

export function rememberFolder(
  drive: string,
  provider: string,
  kind: string,
  folder: string,
): void {
  const memory = _read();
  memory[_key(drive, provider, kind)] = folder;
  _write(memory);
}

export function forgetFolder(
  drive: string,
  provider: string,
  kind: string,
): void {
  const memory = _read();
  delete memory[_key(drive, provider, kind)];
  _write(memory);
}

/** Test-only: wipe the entire memory state. */
export function _resetMemoryForTests(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
