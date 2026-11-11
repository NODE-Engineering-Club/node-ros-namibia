import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { Connection } from '@asket/lib/connection.js';

import { App } from './App.jsx';
import { CloudBadge, CloudSignIn } from './components/Cloud.jsx';
import { createAzureConnection } from './lib/azureTransport.js';
import { loadCloudConfig } from './lib/cloudConfig.js';
import { tapStreamUnavailable } from './lib/streamNotes.js';
import { createMockRig, refuseWhenDown } from './lib/mockRig.js';
import './theme/app.css';
import './theme/screens.css';
import './theme/responsive.css';
import './theme/cloud.css';

/**
 * Same boot as src/asket_gui/src/main.jsx: the only difference between real
 * and mock mode is which socket the (shared, unmodified) Connection opens.
 *
 *   npm run dev        → real backend on :8080 (gui_backend)
 *   npm run dev:mock   → whole cockpit generated in the browser
 *   npm run build:cloud → read-only cockpit fed from Azure (lib/azureTransport.js),
 *                         for a static host such as Vercel or Render
 */
const useMock = import.meta.env.VITE_MOCK === 'true' || import.meta.env.VITE_MOCK === '1';
const useCloud = import.meta.env.VITE_SOURCE === 'azure';

async function boot() {
  const root = createRoot(document.getElementById('root'));
  let connection;
  if (useCloud) {
    const cfg = loadCloudConfig();
    if (!cfg) {
      root.render(<StrictMode><CloudSignIn /></StrictMode>);
      return;
    }
    connection = createAzureConnection(Connection, cfg);
    connection.cloud.from = cfg.from;
  } else if (useMock) {
    const { createMockConnection } = await import('@asket/lib/mock/index.js');
    connection = createMockConnection({ timeScale: 4 });
    // Mock only: handy for poking the simulated store from the dev console.
    window.__asketConnection = connection;
  } else {
    connection = new Connection();
  }
  // `stream_unavailable` is not kept by the shared Connection; record it on
  // the side so panels can say "source silent" (lib/streamNotes.js).
  // Mock only: the redesign's test rig for the checklist items the shared
  // mock cannot produce (lib/mockRig.js). Never attached to a real backend.
  if (useMock) connection.rig = createMockRig();
  tapStreamUnavailable(connection, connection.rig ? { beforeOpen: refuseWhenDown(connection.rig) } : {});
  connection.connect();

  root.render(
    <StrictMode>
      <App connection={connection} />
      {connection.isCloud && <CloudBadge connection={connection} />}
    </StrictMode>,
  );
}

boot();
