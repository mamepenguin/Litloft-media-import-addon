import { listWatch, type WatchItem, type WatchLane } from "./api";

export interface ShelfPageItem {
  id: string;
  byline?: string;
}

export interface ShelfPageAnswer {
  items: ShelfPageItem[];
  cursor: string | null;
}

export type ShelfPageProvider = (args: {
  drive: string;
  locale: string;
  limit: number;
  cursor: string | null;
  restrict: (ids: string[]) => Promise<string[]>;
}) => Promise<ShelfPageAnswer>;

const READ = 60;
// A call reads a bounded amount of the lane so a narrow target cannot make
// one Discover visit walk a whole lane.
const MAX_READS = 10;

/** The calendar date written in `published_at`, as `YYYY-MM-DD`, or null. */
function calendarDate(publishedAt: string): string | null {
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

function bylineOf(item: WatchItem, locale: string): string | undefined {
  const channel = item.channel?.trim() || undefined;
  const day = item.published_at ? calendarDate(item.published_at) : null;
  const date = day
    ? new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(new Date(`${day}T00:00:00Z`))
    : undefined;
  const parts = [channel, date].filter((part): part is string => !!part);
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

async function lanePage(
  lane: WatchLane,
  {
    drive,
    locale,
    limit,
    cursor,
    restrict,
  }: Parameters<ShelfPageProvider>[0],
): Promise<ShelfPageAnswer> {
  const items: ShelfPageItem[] = [];
  let after = cursor ?? undefined;
  let examined: string | null = null;

  for (let read = 0; read < MAX_READS; read += 1) {
    const rows = await listWatch(
      drive,
      lane,
      after === undefined ? { limit: READ } : { limit: READ, after },
    );
    const accepted = new Set(await restrict(rows.map((row) => row.file_id)));
    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      examined = row.cursor;
      if (!accepted.has(row.file_id)) continue;
      const byline = bylineOf(row, locale);
      items.push(byline ? { id: row.file_id, byline } : { id: row.file_id });
      if (items.length === limit) {
        // A short read whose last row is the limit-th taken row has ended the
        // lane; anything after it in a longer read stays unexamined.
        return rows.length < READ && index === rows.length - 1
          ? { items, cursor: null }
          : { items, cursor: row.cursor };
      }
    }
    if (rows.length < READ) return { items, cursor: null };
    after = rows[rows.length - 1].cursor;
  }
  return { items, cursor: examined };
}

export const watchPageProviders: Record<string, ShelfPageProvider> = {
  "media-import-regular": (args) => lanePage("regular", args),
  "media-import-feed": (args) => lanePage("feed", args),
};
