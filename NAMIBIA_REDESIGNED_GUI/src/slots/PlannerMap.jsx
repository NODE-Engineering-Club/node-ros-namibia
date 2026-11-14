import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';

import { buildStyle, graticule } from '@asket/lib/mapStyle.js';
import { registerMockTileProtocol } from '@asket/lib/mock/tiles.js';
import { MapUnavailable, hasWebGL } from '../components/MapGuard.jsx';

import {
  EMPTY, GRID_CENTER, MAX_ZOOM, addAreaLayers, gridBounds, loadGrid, readSatPref, setSatelliteVisible, writeSatPref,
} from '../lib/areaLayers.js';

/**
 * Mission Design basemap.
 *
 * The same tile sources as the live map (offline .mbtiles / cached basemap via
 * gui_backend, synthetic tiles in mock mode), and the same honest fallback —
 * a labelled lat/lon graticule — when there are none. It refits to the route
 * whenever a .json is imported (`fitKey` changes) or "Fit route" is pressed.
 */


function darkStyle() {
  return {
    version: 8,
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#0f1d27' } }],
  };
}

export function styleFor(info, isMock, tileUrls = null) {
  if (isMock) {
    if (!info?.mbtiles?.available) return { style: darkStyle(), tiles: false };
    registerMockTileProtocol(maplibregl);
    return { style: buildStyle({ origin: window.location.origin, mockTileUrl: 'mocktiles://{z}/{x}/{y}' }), tiles: true };
  }
  const mbtiles = Boolean(info?.mbtiles?.available);
  const basemap = Boolean(info?.online) && (info?.upstream?.reachable !== false || (info?.cache?.entries || 0) > 0);
  if (!mbtiles && !basemap) return { style: darkStyle(), tiles: false };
  return { style: buildStyle({ origin: window.location.origin, basemap, mbtiles, tileUrls }), tiles: true };
}

export function PlannerMap({
  connection, waypoints, selected, onSelect, onAdd, onMove, addMode, fitKey,
}) {
  const container = useRef(null);
  const map = useRef(null);
  const markers = useRef([]);
  const handlers = useRef({});
  const [ready, setReady] = useState(false);
  const [tiles, setTiles] = useState(null);
  const [noMap, setNoMap] = useState(null);
  const [satellite, setSatellite] = useState(readSatPref);
  const [satError, setSatError] = useState(false);
  const satRef = useRef(satellite);
  satRef.current = satellite;
  const areaBounds = useRef(null);
  handlers.current = { onAdd, onMove, onSelect, addMode };

  // -- create ----------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let info = null;
      try { info = await connection.fetchTileInfo(); } catch { info = null; }
      // The operating area (Shearwater Bay grid) over satellite imagery, the
      // same layers as Navigate's GPS window (lib/areaLayers.js).
      let grid = EMPTY;
      try { grid = await loadGrid(); } catch { grid = EMPTY; }
      if (cancelled || !container.current) return;
      const bounds = gridBounds(grid);
      areaBounds.current = bounds;
      const { style, tiles: hasTiles } = styleFor(info, connection.isMock, connection.tileUrls);
      setTiles(hasTiles);
      if (!hasWebGL()) { setNoMap('The map needs WebGL, which this browser does not provide.'); return; }
      let m;
      try {
        m = new maplibregl.Map({
        container: container.current,
        style,
        maxZoom: MAX_ZOOM,
        ...(bounds ? { bounds, fitBoundsOptions: { padding: 30 } } : { center: GRID_CENTER, zoom: 14 }),
        attributionControl: { compact: true },
        });
      } catch (err) {
        setNoMap(`The map failed to start (${String(err?.message || err).slice(0, 120)}).`);
        return;
      }
      m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      m.on('load', () => {
        addAreaLayers(m, grid, { satellite: satRef.current, onSatError: setSatError });
        if (!hasTiles) {
          m.addSource('grid', { type: 'geojson', data: graticule(m.getBounds()) });
          m.addLayer({ id: 'grid', type: 'line', source: 'grid', paint: { 'line-color': 'rgba(255,255,255,0.10)', 'line-width': 1 } });
          m.addLayer({
            id: 'grid-label', type: 'symbol', source: 'grid',
            layout: { 'symbol-placement': 'line', 'text-field': ['get', 'label'], 'text-size': 10 },
            paint: { 'text-color': 'rgba(230,237,243,0.55)' },
          });
          m.on('moveend', () => m.getSource('grid')?.setData(graticule(m.getBounds())));
        }
        // Route last, so it draws above the imagery and the area grid.
        m.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        m.addLayer({ id: 'route', type: 'line', source: 'route', paint: { 'line-color': '#f0b429', 'line-width': 3 } });
        setReady(true);
      });
      m.on('click', (e) => {
        if (handlers.current.addMode) handlers.current.onAdd({ lat: +e.lngLat.lat.toFixed(6), lon: +e.lngLat.lng.toFixed(6) });
      });
      map.current = m;
    })();
    return () => {
      cancelled = true;
      markers.current.forEach((mk) => mk.remove());
      map.current?.remove();
      map.current = null;
    };
  }, [connection]);

  // -- route + markers -------------------------------------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    m.getSource('route')?.setData({
      type: 'Feature', properties: {},
      geometry: { type: 'LineString', coordinates: waypoints.map((w) => [w.lon, w.lat]) },
    });
    markers.current.forEach((mk) => mk.remove());
    markers.current = waypoints.map((w, i) => {
      const el = document.createElement('div');
      el.className = [
        'wp-marker', i === selected ? 'selected' : '', i === 0 ? 'start' : '', i === waypoints.length - 1 && i > 0 ? 'end' : '',
      ].join(' ');
      el.textContent = String(i + 1);
      el.addEventListener('click', (ev) => { ev.stopPropagation(); handlers.current.onSelect(i); });
      const mk = new maplibregl.Marker({ element: el, draggable: true }).setLngLat([w.lon, w.lat]).addTo(m);
      mk.on('dragend', () => {
        const p = mk.getLngLat();
        handlers.current.onMove(i, { lat: +p.lat.toFixed(6), lon: +p.lng.toFixed(6) });
      });
      return mk;
    });
  }, [waypoints, selected, ready]);

  // -- fit to route ----------------------------------------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready || waypoints.length === 0) return;
    m.resize();
    if (waypoints.length === 1) { m.flyTo({ center: [waypoints[0].lon, waypoints[0].lat], zoom: 15 }); return; }
    const b = new maplibregl.LngLatBounds();
    waypoints.forEach((w) => b.extend([w.lon, w.lat]));
    m.fitBounds(b, { padding: 60, maxZoom: 17, duration: 600 });
    // Only the explicit triggers refit — not every drag of a pin.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, ready]);

  useEffect(() => {
    if (map.current) map.current.getCanvas().style.cursor = addMode ? 'crosshair' : '';
  }, [addMode]);

  // -- satellite on / off (shared preference with Navigate) -------------
  useEffect(() => {
    if (ready) setSatelliteVisible(map.current, satellite);
    writeSatPref(satellite);
  }, [satellite, ready]);

  const fitArea = () => {
    if (map.current && areaBounds.current) map.current.fitBounds(areaBounds.current, { padding: 30, duration: 500 });
  };

  return (
    <>
      <div ref={container} className="map" />
      {noMap && <MapUnavailable reason={noMap} />}
      <div className="map-tools">
        <button type="button" className="btn small" onClick={fitArea}>Fit grid</button>
        <button type="button" className={`btn small ${satellite ? 'primary' : ''}`} onClick={() => setSatellite((s) => !s)}>
          Satellite {satellite ? 'on' : 'off'}
        </button>
      </div>
      <div className="map-notes">
        {satellite && satError && (
          <div className="map-note warn">Satellite imagery not loading (no internet?).</div>
        )}
        {tiles === false && (!satellite || satError) && (
          <div className="map-note">No map tiles · coordinate grid only</div>
        )}
      </div>
    </>
  );
}
