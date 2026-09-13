import type { SttMode } from "../api";

export const STT_MODE_STORAGE_KEY = "media_import.stt_mode_v1";
export const STT_MODES: SttMode[] = ["manual", "missing_captions", "always"];

export function readStoredSttMode(): SttMode {
  if (typeof window === "undefined") return "manual";
  const raw = window.localStorage.getItem(STT_MODE_STORAGE_KEY);
  return STT_MODES.includes(raw as SttMode) ? (raw as SttMode) : "manual";
}
