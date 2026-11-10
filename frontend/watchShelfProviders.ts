import { listWatch, type WatchLane } from "./api";

export type ShelfProvider = (args: {
  drive: string;
  limit: number;
  restrict: (ids: string[]) => Promise<string[]>;
}) => Promise<string[]>;

async function laneProvider(
  lane: WatchLane,
  { drive, restrict }: Parameters<ShelfProvider>[0],
): Promise<string[]> {
  const items = await listWatch(drive, lane, { limit: 60 });
  return restrict(items.map((item) => item.file_id));
}

export const watchShelfProviders: Record<string, ShelfProvider> = {
  "media-import-regular": (args) => laneProvider("regular", args),
  "media-import-feed": (args) => laneProvider("feed", args),
};
