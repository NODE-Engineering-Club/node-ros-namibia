import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';

import { streamAgeMs, streamPayload } from '@asket/lib/connection.js';
import { staleness, thresholdsForRate } from '@asket/lib/staleness.js';

import { styleFor } from './PlannerMap.jsx';
import { MapBoundary, MapUnavailable, hasWebGL } from '../components/MapGuard.jsx';
import { distanceM } from '../lib/missionFile.js';
import {
  EMPTY, GRID_CENTER, MAX_ZOOM, addAreaLayers, gridBounds as boundsOf, loadGrid, readSatPref, setSatelliteVisible, writeSatPref,
} from '../lib/areaLayers.js';

/**
 * Navigate's GPS window: the Shearwater Bay survey grid over the basemap.
 *
 * The grid comes from Shearwater_Bay.kmz (a Gaia GPS export: 201 cells,
 * rows A–O), converted once to public/maps/shearwater_bay.geojson. The KMZ
 * holds no imagery, so the basemap underneath is the same tile source as the
 * Mission Design map: offline .mbtiles / cached basemap through gui_backend,
 * synthetic tiles in mock mode, and a plain dark background when there are
 * none — the grid still draws, and the map says so.
 *
 * Vessel and track are drawn from the `vessel` and `track` streams. A vessel
 * position that has gone very stale is drawn dimmed and dashed, never as a
 * live marker.
 *
 * The previous live map (slots/GpsSlot.jsx → asket_gui MissionMap) is kept
 * in the tree, unused, to switch back to.
 */

/** The route shown in Navigate, as GeoJSON: one line and numbered points
 * carrying their status (lib/route.js waypointStatus). */
function routeGeo(route) {
  if (!route?.waypoints?.length) return { line: EMPTY, points: EMPTY };
  const coords = route.waypoints.map((w) => [w.lon, w.lat]);
  return {
    line: coords.length > 1
      ? { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } }
      : EMPTY,
    points: {
      type: 'FeatureCollection',
      features: coords.map((c, i) => ({
        type: 'Feature',
        properties: { n: String(i + 1), status: route.statuses?.[i] || 'pending' },
        geometry: { type: 'Point', coordinates: c },
      })),
    },
  };
}

const fmtKm = (m) => (m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`);

/** No WebGL (e.g. the Jetson's own Chromium): say so instead of crashing. */
export function AreaMap(props) {
  if (!hasWebGL()) return <div className="slot area-map"><MapUnavailable /></div>;
  return <MapBoundary><AreaMapInner {...props} /></MapBoundary>;
}

function AreaMapInner({ v, connection, route = null }) {
  const container = useRef(null);
  const map = useRef(null);
  const marker = useRef(null);
  const gridBounds = useRef(null);
  const [ready, setReady] = useState(false);
  const [tiles, setTiles] = useState(null);
  const [gridError, setGridError] = useState('');
  const [follow, setFollow] = useState(false);
  const [satellite, setSatellite] = useState(readSatPref);
  const [satError, setSatError] = useState(false);
  const satRef = useRef(satellite);
  satRef.current = satellite;

  const vessel = v.vessel;
  const track = streamPayload(v.state, 'track');
  const ageMs = streamAgeMs(v.state, 'vessel');
  const stale = staleness(ageMs, ...thresholdsForRate(v.state.subscriptions.vessel?.rate_hz));
  const hasFix = Number.isFinite(vessel?.lat) && Number.isFinite(vessel?.lon);

  // -- grid file (once) ------------------------------------------------
  const [grid, setGrid] = useState(null);
  useEffect(() => {
    let cancelled = false;
    loadGrid()
      .then((fc) => { if (!cancelled) setGrid(fc); })
      .catch((e) => {
        if (!cancelled) { setGridError(`Shearwater Bay grid not loaded (${e.message}).`); setGrid(EMPTY); }
      });
    return () => { cancelled = true; };
  }, []);

  // -- tile availability (polled, like the old map: it changes with the
  // link, the cache and, in mock mode, the dev panel's tiles switch) -------
  const [tileInfo, setTileInfo] = useState(undefined);
  const tileKey = tileInfo === undefined ? null : String(styleFor(tileInfo, connection.isMock, connection.tileUrls).tiles);
  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const poll = async () => {
      let info = null;
      try { info = await connection.fetchTileInfo(); } catch { info = null; }
      if (cancelled) return;
      setTileInfo(info);
      timer = setTimeout(poll, connection.isMock ? 3000 : 20000);
    };
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [connection, v.state.tileGeneration]);

  // Camera carried across a rebuild, so tiles appearing mid-survey do not
  // throw the operator back to the whole grid.
  const camera = useRef(null);

  // -- create (again whenever tile availability changes) ---------------
  useEffect(() => {
    if (!container.current || grid === null || tileKey === null) return undefined;
    const { style, tiles: hasTiles } = styleFor(tileInfo, connection.isMock, connection.tileUrls);
    setTiles(hasTiles);
    const bounds = boundsOf(grid);
    gridBounds.current = bounds;
    {
      const keep = camera.current;
      const m = new maplibregl.Map({
        container: container.current,
        style,
        maxZoom: MAX_ZOOM,
        ...(keep
          ? { center: keep.center, zoom: keep.zoom, bearing: keep.bearing }
          : bounds
            ? { bounds, fitBoundsOptions: { padding: 24 } }
            : { center: GRID_CENTER, zoom: 14 }),
        attributionControl: { compact: true },
      });
      m.addControl(new maplibregl.NavigationControl({ showCompass: true }), 'top-right');
      m.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
      m.on('dragstart', () => setFollow(false));

      m.on('load', () => {
        addAreaLayers(m, grid, { satellite: satRef.current, onSatError: setSatError });
        // Route under the track: where the boat should go, then where it went.
        m.addSource('route-line', { type: 'geojson', data: EMPTY });
        m.addLayer({
          id: 'route-line', type: 'line', source: 'route-line',
          paint: { 'line-color': '#79c0ff', 'line-width': 2, 'line-dasharray': [2, 1.5], 'line-opacity': 0.9 },
        });
        m.addSource('route-points', { type: 'geojson', data: EMPTY });
        m.addLayer({
          id: 'route-points', type: 'circle', source: 'route-points',
          paint: {
            'circle-radius': ['match', ['get', 'status'], 'current', 9, 7],
            'circle-color': ['match', ['get', 'status'],
              'passed', '#3fb950', 'current', '#58a6ff', 'stopped', '#d29922', 'rgba(13,17,23,0.85)'],
            'circle-stroke-color': ['match', ['get', 'status'], 'pending', '#8b949e', '#ffffff'],
            'circle-stroke-width': ['match', ['get', 'status'], 'current', 2.5, 1.5],
          },
        });
        m.addLayer({
          id: 'route-labels', type: 'symbol', source: 'route-points',
          layout: { 'text-field': ['get', 'n'], 'text-size': 10, 'text-allow-overlap': true },
          paint: { 'text-color': '#ffffff' },
        });
        m.addSource('track', { type: 'geojson', data: EMPTY });
        m.addLayer({
          id: 'track', type: 'line', source: 'track',
          paint: { 'line-color': '#3fb950', 'line-width': 2.5, 'line-opacity': 0.9 },
        });
        setReady(true);
      });
      map.current = m;
    }
    // The box changes size without the window doing so (the Navigate column
    // divider, a neighbour folded away): keep the canvas filling it.
    const resize = typeof ResizeObserver === 'undefined' ? null
      : new ResizeObserver(() => { try { map.current?.resize(); } catch { /* removed */ } });
    resize?.observe(container.current);
    return () => {
      resize?.disconnect();
      const m = map.current;
      try {
        if (m) camera.current = { center: m.getCenter().toArray(), zoom: m.getZoom(), bearing: m.getBearing() };
      } catch { /* never finished loading */ }
      marker.current?.remove();
      marker.current = null;
      m?.remove();
      map.current = null;
      setReady(false);
    };
    // tileInfo is read through tileKey: rebuild only when availability flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection, grid, tileKey]);

  // -- vessel + track --------------------------------------------------
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    m.getSource('track')?.setData(
      track?.points?.length
        ? { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: track.points } }
        : EMPTY,
    );
    if (!hasFix) {
      marker.current?.remove();
      marker.current = null;
      return;
    }
    if (!marker.current) {
      const el = document.createElement('div');
      el.className = 'area-vessel';
      el.innerHTML = '<span class="ring"></span><svg viewBox="0 0 24 24"><path d="M12 2 L19 21 L12 17 L5 21 Z"/></svg>';
      marker.current = new maplibregl.Marker({ element: el, rotationAlignment: 'map' }).setLngLat([vessel.lon, vessel.lat]).addTo(m);
    }
    const headingOk = vessel.heading_valid !== false && Number.isFinite(vessel.heading_deg);
    marker.current.setLngLat([vessel.lon, vessel.lat]).setRotation(headingOk ? vessel.heading_deg : 0);
    const el = marker.current.getElement();
    el.classList.toggle('no-heading', !headingOk);
    el.classList.toggle('stale', stale === 'stale');
    el.classList.toggle('very-stale', stale === 'very-stale');
    if (follow) m.easeTo({ center: [vessel.lon, vessel.lat], duration: 400 });
  }, [ready, vessel, track, hasFix, stale, follow]);

  // -- route + waypoint status ------------------------------------------
  const geo = routeGeo(route);
  const routeKey = JSON.stringify(geo.points.features.map((f) => [f.geometry.coordinates, f.properties.status]));
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    m.getSource('route-line')?.setData(geo.line);
    m.getSource('route-points')?.setData(geo.points);
    // geo is derived from routeKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, routeKey]);

  const fitRoute = () => {
    const m = map.current;
    if (!m || !route?.waypoints?.length) return;
    setFollow(false);
    const b = new maplibregl.LngLatBounds();
    route.waypoints.forEach((w) => b.extend([w.lon, w.lat]));
    m.fitBounds(b, { padding: 40, maxZoom: 18, duration: 500 });
  };

  // -- satellite on / off ----------------------------------------------
  useEffect(() => {
    const m = map.current;
    if (ready) setSatelliteVisible(m, satellite);
    writeSatPref(satellite);
  }, [satellite, ready]);

  const fitGrid = () => {
    setFollow(false);
    if (map.current && gridBounds.current) map.current.fitBounds(gridBounds.current, { padding: 24, duration: 500 });
  };

  // How far the vessel is from the grid, when it is not on it (in mock mode
  // the simulated vessel sails off Walvis Bay, ~400 km north).
  let away = null;
  if (hasFix && gridBounds.current) {
    const b = gridBounds.current;
    const inside = vessel.lon >= b.getWest() - 0.005 && vessel.lon <= b.getEast() + 0.005
      && vessel.lat >= b.getSouth() - 0.005 && vessel.lat <= b.getNorth() + 0.005;
    if (!inside) {
      const c = b.getCenter();
      away = distanceM({ lat: vessel.lat, lon: vessel.lon }, { lat: c.lat, lon: c.lng });
    }
  }

  return (
    <div className="slot area-map">
      <div ref={container} className="area-map-canvas" />
      <div className="map-tools">
        <button type="button" className={`btn small ${follow ? 'primary' : ''}`} disabled={!hasFix} onClick={() => setFollow((f) => !f)}>
          {follow ? 'Following vessel' : 'Follow vessel'}
        </button>
        <button type="button" className="btn small" onClick={fitGrid}>Fit grid</button>
        {route?.waypoints?.length > 0 && (
          <button type="button" className="btn small" onClick={fitRoute}>Fit route</button>
        )}
        <button type="button" className={`btn small ${satellite ? 'primary' : ''}`} onClick={() => setSatellite((s) => !s)}>
          Satellite {satellite ? 'on' : 'off'}
        </button>
      </div>
      <div className="map-notes">
        {gridError && <div className="map-note alarm">{gridError}</div>}
        {satellite && satError && (
          <div className="map-note warn">Satellite imagery not loading (no internet?).</div>
        )}
        {tiles === false && (!satellite || satError) && (
          <div className="map-note">No basemap tiles available: grid only.</div>
        )}
        {!hasFix && <div className="map-note">No vessel position.</div>}
        {away != null && <div className="map-note">Vessel is {fmtKm(away)} from the Shearwater Bay grid.</div>}
      </div>
    </div>
  );
}
