import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

// Where `npm run dev` sends /ws, /api and /tiles. The simulator backend
// listens on 8080 (the default); the boat's gui_backend serves on 8090:
//   BACKEND_URL=http://<jetson-ip>:8090 npm run dev
const BACKEND_URL = (process.env.BACKEND_URL || 'http://localhost:8080').replace(/\/+$/, '');
const BACKEND_WS = BACKEND_URL.replace(/^http/, 'ws');

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
        : mode === 'cloud'
          // Same rewrites as vercel.json / render.yaml: the map's /tiles/...
          // URLs go to the public tile servers gui_backend proxies on the boat.
          ? {
              '/tiles/source/basemap': {
                target: 'https://tiles.openfreemap.org', changeOrigin: true,
                rewrite: (p) => p.replace(/^\/tiles\/source\/basemap/, '/planet/latest'),
              },
              '/tiles/fonts': {
                target: 'https://tiles.openfreemap.org', changeOrigin: true,
                rewrite: (p) => p.replace(/^\/tiles\/fonts/, '/fonts'),
              },
              '/tiles/source/seamark': {
                target: 'https://tiles.openseamap.org', changeOrigin: true,
                rewrite: (p) => p.replace(/^\/tiles\/source\/seamark/, '/seamark'),
              },
            }
          : {
            '/ws': { target: BACKEND_WS, ws: true },
            '/api': BACKEND_URL,
            '/tiles': BACKEND_URL,
            },
  },
}));
