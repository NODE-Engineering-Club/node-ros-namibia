import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { Connection } from '@asket/lib/connection.js';

import { App } from './App.jsx';
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
  connection.connect();

  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <App connection={connection} />
    </StrictMode>,
  );
}

boot();
