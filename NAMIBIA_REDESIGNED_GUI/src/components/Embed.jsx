import { DetailsContext } from '@asket/lib/details.js';

import '../theme/asket-embed.css';

/**
 * A component reused from src/asket_gui, rendered inside the glass frame.
 *
 * The component keeps its own logic and class names; `asket-embed.css` skins
 * them only inside this wrapper (the same pattern as legacy-map.css for the
 * map), so the original cockpit is unaffected.
 *
 * `details` defaults to true: the redesign has no Simple/Details toggle, and
 * the Details lines are the ones that say what a control actually does (the
 * propulsion cut's effect, "the hardware killswitch and RC channel 8 always
 * work", "health not monitored", which streams the link no longer carries).
 * Hiding them would make the redesign say less than the original.
 */
export function AsketEmbed({ children, details = true, className = '' }) {
  return (
    <DetailsContext.Provider value={details}>
      <div className={`asket-embed ${className}`}>{children}</div>
    </DetailsContext.Provider>
  );
}
