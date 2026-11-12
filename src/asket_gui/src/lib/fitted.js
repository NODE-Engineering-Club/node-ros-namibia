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

/**
 * The data is simulated: the browser mock, or a backend started with
 * `use_sim:=true`. Survey lines, coverage and obstacle returns exist only
 * there (nothing aboard the real boat produces them yet), so the map draws and
 * lists those layers only in that case.
 */
export function isSimulated(state, connection) {
  const mode = state.hello?.source?.mode;
  return Boolean(connection?.isMock) || mode === 'sim' || mode === 'mock';
}
