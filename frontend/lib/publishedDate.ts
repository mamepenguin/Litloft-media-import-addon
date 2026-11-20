/** The calendar date written in `publishedAt`, as `YYYY-MM-DD`, or null. */
export function calendarDate(publishedAt: string | null): string | null {
  if (publishedAt === null) return null;
  const value = publishedAt.trim();
  let day: string;
  if (/^\d{8}/.test(value)) {
    day = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  } else if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    day = value.slice(0, 10);
  } else {
    return null;
  }
  return Number.isNaN(Date.parse(`${day}T00:00:00Z`)) ? null : day;
}

/**
 * The date part of the Watch shelves' byline.
 *
 * UTC keeps the calendar date in `publishedAt` from sliding to the day before
 * or after in the browser's own zone.
 */
export function formatPublishedDate(
  publishedAt: string | null,
  locale: string,
): string | null {
  const day = calendarDate(publishedAt);
  if (day === null) return null;
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${day}T00:00:00Z`));
}
