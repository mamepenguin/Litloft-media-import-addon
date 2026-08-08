"use client";

import { useTranslations } from "next-intl";
import { Check } from "lucide-react";

export interface PlayerUiToggleProps {
  youtubeUi: boolean;
  onChange: (youtubeUi: boolean) => void;
}

/**
 * Chooses which player UI is on screen: Litloft's own controls, or
 * YouTube's.
 *
 * Worth being plain about what this does and does not control. It sets
 * `playerVars.controls` and drops `playsinline`; that is the whole
 * mechanism. On iOS the browser then refuses inline playback and opens
 * its own full-screen player, which is where a Picture-in-Picture
 * button lives — unreachable otherwise, since a cross-origin iframe
 * puts its `<video>` beyond our reach. That outcome is the reason to
 * use this, but it belongs to the platform, not to us, so the label
 * describes the choice we actually make.
 *
 * Rendered into the settings sheet through MediaControls' `settingsExtra`
 * slot, which core keeps opaque so it needs no notion of YouTube.
 */
export function PlayerUiToggle({ youtubeUi, onChange }: PlayerUiToggleProps) {
  const t = useTranslations("mediaImport.player");

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="px-1 text-sm">{t("playerUi")}</span>
      <button
        type="button"
        role="switch"
        aria-checked={youtubeUi}
        aria-label={t("playerUi")}
        onClick={() => onChange(!youtubeUi)}
        className={[
          "inline-flex h-11 min-w-24 items-center justify-center gap-1 rounded-2xl px-3 text-sm",
          "transition-colors motion-reduce:transition-none",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring",
          youtubeUi ? "bg-white/20 font-medium" : "hover:bg-white/10",
        ].join(" ")}
      >
        {youtubeUi && <Check size={14} aria-hidden="true" />}
        {youtubeUi ? t("playerUiYouTube") : t("playerUiLitloft")}
      </button>
    </div>
  );
}
