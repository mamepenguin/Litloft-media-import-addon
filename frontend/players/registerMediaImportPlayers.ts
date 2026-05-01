/**
 * Side-effect module: registers Media Import's official .loft players.
 *
 * Imported by ``slots.ts`` and ``Page.tsx`` so that any time the addon's
 * frontend module graph is loaded, the players are registered against
 * core's ``playerRegistry``. Together with ``AddonSlotsProvider``'s
 * eager preload of every active addon's slot module, this ensures
 * registration completes before LoftPlayer first dispatches.
 *
 * Importing this module triggers the registrations exactly once due to
 * ES module evaluation semantics. The module has no exports.
 */

import { registerLoftPlayer } from "@/components/loft/playerRegistry";
import YouTubeEmbed from "./YouTubeEmbed";
import VimeoEmbed from "./VimeoEmbed";

registerLoftPlayer("youtube", YouTubeEmbed);
registerLoftPlayer("vimeo", VimeoEmbed);
