import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { Connection } from '@asket/lib/connection.js';

import { App } from './App.jsx';
import { tapStreamUnavailable } from './lib/streamNotes.js';
import { createMockRig, refuseWhenDown } from './lib/mockRig.js';
import './theme/app.css';
import './theme/screens.css';

/**
 * Same boot as src/asket_gui/src/main.jsx: the only difference between real
 * and mock mode is which socket the (shared, unmodified) Connection opens.
 *
 *   npm run dev        → real backend on :8080 (gui_backend)
 *   npm run dev:mock   → whole cockpit generated in the browser
 */
const useMock = import.meta.env.VITE_MOCK === 'true' || import.meta.env.VITE_MOCK === '1';

async function boot() {
  let connection;
  if (useMock) {
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

  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App connection={connection} />
    </StrictMode>,
  );
}

boot();
