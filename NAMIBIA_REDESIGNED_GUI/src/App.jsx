import { useEffect, useState } from 'react';

import { isFitted } from '@asket/lib/fitted.js';

import { Background, ROUTES, Sidebar, Splash, SystemFooter } from './components/Shell.jsx';
import { Alerts } from './screens/Alerts.jsx';
import { MissionDesign } from './screens/MissionDesign.jsx';
import { Navigate } from './screens/Navigate.jsx';
import { Preflight } from './screens/Preflight.jsx';
import { Repo } from './screens/Repo.jsx';
import { newMission } from './lib/missionFile.js';
import { useMissionLibrary } from './lib/missionStore.js';
import { SUBSCRIPTIONS, useVessel } from './lib/vessel.js';

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
    const absent = [!lidarFitted && 'lidar', !cameraFitted && 'camera'].filter(Boolean);
    connection.subscribe(SUBSCRIPTIONS.filter((s) => !absent.includes(s.name)));
    if (absent.length) connection.unsubscribe(absent);
  }, [connection, lidarFitted, cameraFitted]);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const navigate = (id) => { window.location.hash = id; };
  const endSplash = () => { sessionStorage.setItem('asket.redesign.splash', '1'); setSplash(false); };

  const openInDesigner = (mission) => { setDraft(mission); navigate('mission'); };
  const launch = (mission) => { library.setActive(mission.id); navigate('navigate'); };

  const bringUpHealthy = () => {
    // Mirrors the "Everything working" control in src/asket_gui's dev panel,
    // through the ordinary command path.
    connection.setProfile('auto');
    connection.command('set_mode', { mode: 'AUTONOMOUS' });
    connection.command('start_mission', { name: 'demo' });
    connection.command('run_system_test', {});
  };

  const screen = {
    navigate: <Navigate v={v} connection={connection} active={library.active} onNavigate={navigate} />,
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
          <main className="workspace">
            {screen}
            <SystemFooter v={v} connection={connection}>
              {connection.isMock && (
                <button
                  type="button" className="btn small" onClick={bringUpHealthy}
                  style={{ height: 20, fontSize: 10 }}
                  title="Mock mode: put the simulated boat autonomous, recording and pre-flighted."
                >
                  Mock: everything working
                </button>
              )}
            </SystemFooter>
          </main>
        </div>
      )}
    </>
  );
}
