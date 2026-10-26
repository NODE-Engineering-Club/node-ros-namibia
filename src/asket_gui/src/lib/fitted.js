/**
 * Whether the vessel carries an optional sensor, as the backend's `hello`
 * says (`source.fitted`, from topics.yaml and the launch arguments).
 *
 * Unknown — the browser mock, the simulator, an older backend — counts as
 * fitted: hiding a panel on a guess would be worse than showing an empty one.
 */
export function isFitted(state, sensor) {
  return state.hello?.source?.fitted?.[sensor] !== false;
}
