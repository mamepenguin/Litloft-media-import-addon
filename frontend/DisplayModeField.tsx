"use client";

import { useTranslations } from "next-intl";

import { DISPLAY_MODES, type DisplayMode } from "./api";

interface Props {
  value: DisplayMode;
  onChange: (mode: DisplayMode) => void;
  disabled?: boolean;
  /** Distinguishes radio groups when several are on the page. */
  name: string;
}

/**
 * Picks how prominently a subscription's new videos appear in Watch.
 *
 * A radio group rather than a select: the three options are not
 * interchangeable settings but a statement of intent, and each needs a
 * line of explanation next to it. `library` is listed first and is the
 * default — a subscription reaches Watch only because the user put it
 * there.
 *
 * Spec: 2026-08-10-media-import-watch-surface.md §3.2.
 */
export default function DisplayModeField({
  value,
  onChange,
  disabled,
  name,
}: Props) {
  const t = useTranslations("mediaImport.displayMode");

  return (
    <fieldset className="min-w-0" disabled={disabled}>
      <legend className="mb-1.5 text-xs font-medium text-text-primary">
        {t("legend")}
      </legend>
      <div className="space-y-1.5">
        {DISPLAY_MODES.map((mode) => {
          const id = `${name}-${mode}`;
          return (
            <label
              key={mode}
              htmlFor={id}
              className={[
                "flex cursor-pointer items-start gap-2.5 rounded-xl px-2.5 py-2 transition-colors",
                value === mode ? "bg-bg-elevated" : "hover:bg-bg-elevated",
                disabled ? "cursor-not-allowed opacity-60" : "",
              ].join(" ")}
            >
              <input
                id={id}
                type="radio"
                name={name}
                value={mode}
                checked={value === mode}
                onChange={() => onChange(mode)}
                className="mt-0.5 accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
              />
              <span className="min-w-0">
                <span className="block text-sm text-text-primary">
                  {t(`${mode}.label`)}
                </span>
                <span className="mt-0.5 block text-xs text-text-muted">
                  {t(`${mode}.hint`)}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
