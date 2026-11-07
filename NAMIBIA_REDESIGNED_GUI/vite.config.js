import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// The redesign is a new presentation layer on top of the existing, tested
// functional layer in src/asket_gui (connection, mock transport, readiness,
// live map). `@asket` points at that source so nothing is copied or forked:
// a fix there is a fix here. Nothing in src/asket_gui is modified.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  resolve: {
    alias: { '@asket': here('../src/asket_gui/src') },
    // Files imported from ../src/asket_gui resolve their packages from THIS
    // folder's node_modules, so there is one React and one MapLibre.
    dedupe: ['react', 'react-dom', 'maplibre-gl'],
  },
  build: {
    // Deliberately NOT gui_backend/static: the existing cockpit stays the one
    // the backend serves until the team decides to switch (see README).
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
  },
  server: {
    port: 5174,
    fs: { allow: [here('..')] },
    proxy:
      mode === 'mock'
        ? undefined
        : {
            '/ws': { target: 'ws://localhost:8080', ws: true },
            '/api': 'http://localhost:8080',
            '/tiles': 'http://localhost:8080',
          },
  },
}));
