import { Chip, Panel } from './Panel.jsx';

/**
 * The panel of a sensor this boat does not carry.
 *
 * Not an empty plot and not a blank picture: with no lidar, an empty obstacle
 * view reads "nothing in range", which is the one thing it cannot know. This
 * says the sensor is absent on purpose, so it never looks like a fault and
 * never looks like clear water.
 */
export function NotFitted({ title, id, children }) {
  return (
    <Panel
      title={title}
      id={id}
      aside={<Chip title="Not on this boat. Not a fault.">not fitted</Chip>}
      summary={<p className="hint" style={{ margin: 0 }}>{children}</p>}
    />
  );
}
