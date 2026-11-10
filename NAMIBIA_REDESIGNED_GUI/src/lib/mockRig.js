import { clearSilent, markSilent } from './streamNotes.js';

/**
 * Mock mode only: the cases from the developer guide's section 6 checklist
 * that the shared mock (src/asket_gui/src/lib/mock) cannot produce on its
 * own, so they can be walked through in the redesign by hand.
 *
 *  - Item 7, "disconnect the backend": the mock's "Link lost" fault keeps
 *    the socket open and keeps sending, so it never produces "no data for
 *    N s" or a closed socket. `freeze` stops the transport delivering
 *    anything while the socket stays open (what a dead link looks like until
 *    TCP notices); `backendDown` closes it and refuses every reconnect.
 *  - Item 8, "the sonar panel with omniscan_bridge absent": the mock's hello
 *    says nothing about sonar_panel, so it always gets the full panel. The
 *    sonar view can be switched to the boat's health view, and to the health
 *    view with no sonar stream at all (the bridge not running), including
 *    the stream_unavailable the real backend would send.
 *
 * None of this is reachable against a real backend: App renders the rig only
 * when `connection.isMock`, and main.jsx attaches it only in mock mode.
 */
export function createMockRig() {
  return { backendDown: false, frozen: false, sonarView: 'as-sent' };
}

/** For tapStreamUnavailable's beforeOpen: a backend that is down. */
export function refuseWhenDown(rig) {
  return () => {
    if (rig.backendDown) throw new Error('backend down (mock test rig)');
  };
}

export function setFrozen(connection, rig, frozen) {
  rig.frozen = frozen;
  const transport = connection.mock?.transport;
  // 1 = OPEN. Anything else and MockTransport emits nothing, while the
  // Connection still believes the socket is up.
  if (transport) transport.readyState = frozen ? 2 : 1;
}

export function setBackendDown(connection, rig, down) {
  rig.backendDown = down;
  if (down) {
    setFrozen(connection, rig, false);
    connection.mock?.transport?.close();
  }
  // Coming back up: the Connection's own reconnect timer opens a new
  // transport on its next attempt.
}

const BRIDGE_ABSENT_REASON =
  'subscribed and granted, but no sonar data has been produced. '
  + 'The node that publishes it may not be running.';

export function setSonarView(connection, rig, view) {
  rig.sonarView = view;
  if (view === 'health-absent') markSilent(connection, 'sonar', BRIDGE_ABSENT_REASON);
  else clearSilent(connection, 'sonar');
}

/**
 * The state the sonar panel is given. 'as-sent' is the real store, untouched.
 * The other two present it as the boat's backend would: sonar_panel 'health',
 * and for 'health-absent' no sonar stream at all.
 */
export function sonarViewState(state, view) {
  if (!view || view === 'as-sent') return state;
  const hello = {
    ...(state.hello || {}),
    source: { ...(state.hello?.source || {}), sonar_panel: 'health' },
  };
  if (view === 'health') return { ...state, hello };
  const { sonar: _dropped, ...streams } = state.streams;
  return { ...state, hello, streams };
}
