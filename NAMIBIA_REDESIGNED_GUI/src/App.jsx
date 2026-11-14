import { useEffect, useState } from 'react';

import { isFitted } from '@asket/lib/fitted.js';
import { MockControls } from '@asket/components/MockControls.jsx';
import { duration } from '@asket/lib/format.js';

import { Background, ROUTES, ShellContext, Sidebar, Splash, SystemFooter } from './components/Shell.jsx';
import { Alerts } from './screens/Alerts.jsx';
import { MissionDesign } from './screens/MissionDesign.jsx';
import { Navigate } from './screens/Navigate.jsx';
import { Preflight } from './screens/Preflight.jsx';
import { Repo } from './screens/Repo.jsx';
import { newMission } from './lib/missionFile.js';
import { useMissionLibrary } from './lib/missionStore.js';
import { SUBSCRIPTIONS, useVessel } from './lib/vessel.js';
import { setBackendDown, setFrozen, setSonarView, sonarViewState } from './lib/mockRig.js';

/**
 * Link loss, said as link loss (developer guide section 3: "a frozen screen
 * that looks live is the worst failure this GUI can have"). Shown on every
 * screen, above everything, with the time since anything was RECEIVED: a
 * socket can stay open long after the link behind it has gone.
 *
 * Plus the original cockpit's "starved" banner: connected, but the server is
 * serving no streams, or has sent a notice saying why nothing arrives.
 */
function LinkBanner({ v }) {
  const { state, receiving, sinceMs } = v;
  if (state.connected && receiving) {
    const subs = Object.values(state.subscriptions || {});
    const starved = subs.length > 0 && !subs.some((x) => x.granted);
    if (state.notice) {
      return (
        <div className="link-banner warn" role="alert">
          <strong>{state.notice.text}</strong>
          {state.notice.detail && <span> {state.notice.detail}</span>}
        </div>
      );
    }
    if (starved) {
      const refused = subs.find((x) => x.reason);
      return (
        <div className="link-banner warn" role="alert">
          <strong>Connected, but the server is serving no streams.</strong>
          <span> {refused?.reason || `link profile ${state.profile?.profile ?? '?'}`}</span>
        </div>
      );
    }
    return null;
  }
  if (!state.lastMessageAt) {
    return (
      <div className="link-banner info" role="status">
        <strong>Connecting to the vessel…</strong>
        <span> Nothing has been received yet; no value on screen is live.</span>
      </div>
    );
  }
  return (
    <div className="link-banner alarm" role="alert">
      <strong>
        {state.connected
          ? `Link lost: nothing received for ${duration((sinceMs ?? 0) / 1000)}`
          : `Link lost: disconnected${state.attempts ? `, retrying (attempt ${state.attempts})` : ''}`}
      </strong>
      <span>
        {' '}Every value on screen is the last one received, and its age keeps growing.
        The RC transmitter and the hardware killswitch do not depend on this link.
      </span>
    </div>
  );
}

/**
 * Mock only, under the shared MockControls: the redesign's test rig
 * (lib/mockRig.js) for checklist items 7 and 8. Styled as the dev panel.
 */
function RigControls({ connection, rig, onChange }) {
  const act = (fn) => () => { fn(); onChange(); };
  const views = [
    ['as-sent', 'As the mock sends it (full)'],
    ['health', 'Boat: health view'],
    ['health-absent', 'Boat: omniscan_bridge not running'],
  ];
  return (
    <div className="mock-body rig">
      <div className="mock-group">
        <h3>Redesign test rig: backend</h3>
        <div className="button-row">
          <button type="button" className={rig.frozen ? 'mock-on' : ''} onClick={act(() => setFrozen(connection, rig, !rig.frozen))}>
            Backend stops sending
          </button>
          <button type="button" className={rig.backendDown ? 'mock-on' : ''} onClick={act(() => setBackendDown(connection, rig, !rig.backendDown))}>
            Backend down
          </button>
        </div>
        <p className="hint" style={{ margin: '4px 0 0' }}>
          Stops sending: socket open, nothing arrives (checklist 7, the case TCP takes
          tens of seconds to notice). Down: socket closed, every reconnect refused.
        </p>
      </div>
      <div className="mock-group">
        <h3>Redesign test rig: sonar panel</h3>
        <div className="button-row">
          {views.map(([id, label]) => (
            <button key={id} type="button" className={rig.sonarView === id ? 'mock-on' : ''} onClick={act(() => setSonarView(connection, rig, id))}>
              {label}
            </button>
          ))}
        </div>
        <p className="hint" style={{ margin: '4px 0 0' }}>
          The boat sends sonar_panel: health. With the bridge not running there is no
          sonar stream at all (checklist 8): the panel must say health is not
          monitored, never show a healthy sonar. Combine with the dev panel&rsquo;s
          &ldquo;Sonar dropout&rdquo; for &ldquo;sonar silent&rdquo;.
        </p>
      </div>
    </div>
  );
}

/**
 * The mock-mode dev panel is not part of the cockpit: no column, no rail.
 * It exists only in mock mode, opens with Ctrl+Shift+D (or the `?dev` URL
 * flag), closes with Escape or the same shortcut, and floats over the screen
 * as a glass drawer.
 */
function useDevDrawer(enabled) {
  const [open, setOpen] = useState(
    () => enabled && new URLSearchParams(window.location.search).has('dev'),
  );
  useEffect(() => {
    if (!enabled) return undefined;
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
  return [open, setOpen];
}

const DRAFT_KEY = 'asket.redesign.draft.v1';

function routeFromHash() {
  const id = (window.location.hash || '').replace(/^#\/?/, '').split('/')[0];
  return ROUTES.some((r) => r.id === id) ? id : 'preflight';
}

function loadDraft() {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return newMission('WALVIS_SURVEY_01');
}

export function App({ connection }) {
  const v = useVessel(connection);
  const library = useMissionLibrary();
  const [route, setRoute] = useState(routeFromHash);
  const [splash, setSplash] = useState(() => !sessionStorage.getItem('asket.redesign.splash'));
  const [draft, setDraftState] = useState(loadDraft);

  const setDraft = (d) => {
    setDraftState(d);
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d)); } catch { /* ignore */ }
  };

  // Same subscription rule as src/asket_gui: a sensor the boat does not carry
  // is never asked for, and is withdrawn once `hello` says it is absent.
  const lidarFitted = isFitted(v.state, 'lidar');
  const cameraFitted = isFitted(v.state, 'camera');
  useEffect(() => {
    const absent = [!lidarFitted && 'lidar', !cameraFitted && 'camera', !cameraFitted && 'camera_seg']
      .filter(Boolean);
    connection.subscribe(SUBSCRIPTIONS.filter((s) => !absent.includes(s.name)));
    if (absent.length) connection.unsubscribe(absent);
  }, [connection, lidarFitted, cameraFitted]);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // Every panel greys out and gets a red edge, not just the footer pill.
  const linkLost = !v.state.connected || !v.receiving;

  const navigate = (id) => { window.location.hash = id; };
  const [mockOpen, setMockOpen] = useDevDrawer(connection.isMock);
  const [, redrawRig] = useState(0);
  const rig = connection.rig;
  const sonarState = rig ? sonarViewState(v.state, rig.sonarView) : v.state;
  const endSplash = () => { sessionStorage.setItem('asket.redesign.splash', '1'); setSplash(false); };

  const openInDesigner = (mission) => { setDraft(mission); navigate('mission'); };
  const launch = (mission) => { library.setActive(mission.id); navigate('navigate'); };

  const screen = {
    navigate: (
      <Navigate v={v} connection={connection} active={library.active} onNavigate={navigate} sonarState={sonarState} />
    ),
    preflight: <Preflight v={v} connection={connection} onNavigate={navigate} />,
    mission: <MissionDesign v={v} connection={connection} draft={draft} setDraft={setDraft} library={library} />,
    alerts: <Alerts v={v} onNavigate={navigate} />,
    repo: <Repo v={v} library={library} onOpenInDesigner={openInDesigner} onLaunch={launch} />,
  }[route];

  return (
    <>
      <Background />
      {splash ? (
        <Splash onDone={endSplash} />
      ) : (
        <div className="shell">
          <Sidebar route={route} onNavigate={navigate} alarms={v.alarms} />
          <main className={`workspace ${linkLost ? 'link-lost' : ''}`}>
            <LinkBanner v={v} />
            <ShellContext.Provider value={{ v, connection }}>{screen}</ShellContext.Provider>
            <SystemFooter />
          </main>
          {connection.isMock && mockOpen && (
            <aside className="glass dev-drawer" aria-label="Developer panel, mock mode only">
              <header className="dev-drawer-head">
                <span>Dev panel</span>
                <span className="faint">mock only · Ctrl+Shift+D</span>
                <button type="button" className="btn small ghost" onClick={() => setMockOpen(false)} aria-label="Close dev panel">✕</button>
              </header>
              <div className="dev-drawer-body">
                <MockControls connection={connection} open onOpenChange={setMockOpen} />
                {rig && <RigControls connection={connection} rig={rig} onChange={() => redrawRig((n) => n + 1)} />}
              </div>
            </aside>
          )}
        </div>
      )}
    </>
  );
}
