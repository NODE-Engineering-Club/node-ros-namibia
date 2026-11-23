/**
 * Route progress, as mission_manager reports it (/mission/status, relayed by
 * gui_backend inside the `mission` stream as `mission.route`).
 *
 * `current_waypoint` is the index of the waypoint being steered to, which is
 * also how many are already reached. Nothing here is inferred from the boat's
 * position: a waypoint is "passed" because the navigation stack said so.
 */

export const ROUTE_LEVEL = {
  RUNNING: 'info', SUCCEEDED: 'ok', ABORTED: 'warn', FAILED: 'alarm', IDLE: 'neutral',
};

const ROUTE_TEXT = {
  RUNNING: 'Following route', SUCCEEDED: 'Route complete', ABORTED: 'Route aborted',
  FAILED: 'Route failed', IDLE: 'No route running',
};

/**
 * Why a mission cannot start right now, or null. A hint for the buttons: the
 * backend re-checks all of it from the vessel's own report (hub _start_blocker).
 */
export function startBlock(v, connection) {
  const pico = v.pico;
  if (connection.isMock || v.isCloud) return 'Missions start only from the cockpit on the boat network.';
  if (isRunning(routeOf(v))) return 'A mission is running. Abort it first.';
  if (!v.state.connected) return 'Not connected to the vessel.';
  if (v.report?.go !== true) return 'Run the preflight: Start unlocks on GO.';
  if (pico?.rc_link_ok !== true) return 'No RC link.';
  if (!(pico?.rc_channel8_raw >= 1400)) return 'Put the RC mode switch (CH3) in AUTO.';
  if (pico?.armed !== true) return pico?.arming_block || 'The vessel is not armed.';
  return null;
}

export function routeOf(v) {
  return v.mission?.route || null;
}

/** Pill for the route state. Unknown is said as unknown, never as idle. */
export function routePill(route) {
  if (!route || route.state == null) return { level: 'neutral', text: 'Route: not reported' };
  return {
    level: ROUTE_LEVEL[route.state] || 'warn',
    text: ROUTE_TEXT[route.state] || route.state,
  };
}

export const isRunning = (route) => route?.state === 'RUNNING';

/** 'passed' | 'current' | 'stopped' | 'pending' for waypoint `i` (0-based). */
export function waypointStatus(route, i) {
  const cur = route?.current_waypoint;
  if (!route || cur == null) return 'pending';
  if (route.state === 'SUCCEEDED') return 'passed';
  if (i < cur) return 'passed';
  if (i === cur) {
    if (route.state === 'RUNNING') return 'current';
    if (route.state === 'ABORTED' || route.state === 'FAILED') return 'stopped';
  }
  return 'pending';
}

export const WAYPOINT_LABEL = {
  passed: 'Passed', current: 'Heading to', stopped: 'Not reached', pending: 'Pending',
};

/**
 * Whether the deployment record (what this server sent) is the route the
 * vessel is reporting on: same length, and a state that only follows a start.
 */
export function deployedIsLive(route, deployed) {
  return Boolean(
    route && deployed
    && ['RUNNING', 'SUCCEEDED', 'ABORTED', 'FAILED'].includes(route.state)
    && route.total_waypoints === deployed.waypoint_count,
  );
}
