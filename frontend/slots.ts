import { lazy } from "react";

// Side-effect: register Media Import's official .loft players (YouTube,
// Vimeo) against core's playerRegistry. Importing this module on slot
// load gives the registrations a chance to fire before LoftPlayer
// dispatches the .loft for the same file.
import "./players/registerMediaImportPlayers";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const slotComponents: Record<string, React.LazyExoticComponent<React.ComponentType<any>>> = {
  "loft-metadata": lazy(() => import("./LoftMetadataPanel")),
};
