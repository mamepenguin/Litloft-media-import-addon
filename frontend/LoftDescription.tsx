"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

export default function LoftDescription({ text }: { text: string }) {
  const t = useTranslations("mediaImport.loftMetadata");
  const ref = useRef<HTMLParagraphElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || expanded) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, expanded]);

  // Clipped lines stay in the DOM, and iOS WebKit paints a selection that
  // reaches them outside the clip, so they must not be selectable.
  const clipped = overflows && !expanded;

  return (
    <div className="mt-1">
      <p
        ref={ref}
        className={[
          "whitespace-pre-wrap",
          expanded ? "" : "line-clamp-3",
          clipped ? "cursor-pointer select-none" : "",
        ].join(" ")}
        onClick={clipped ? () => setExpanded(true) : undefined}
      >
        {text}
      </p>
      {overflows && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
          className="inline-flex items-center font-medium text-text-primary hover:underline pointer-coarse:min-h-11"
        >
          {expanded ? t("showLess") : t("showMore")}
        </button>
      )}
    </div>
  );
}
