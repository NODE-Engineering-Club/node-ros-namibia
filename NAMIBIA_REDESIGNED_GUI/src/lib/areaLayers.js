import maplibregl from 'maplibre-gl';

/**
 * The operating-area layers shared by every map in the redesign (Navigate's
 * GPS window and the Mission Design planner):
 *
 *  - Satellite imagery: Esri World Imagery, fetched by the browser straight
 *    from the internet (not through gui_backend, so not cached for offline
 *    use; needs the operator's machine to have internet). Drawn above any
 *    gui_backend basemap and below everything else.
 *  - The Shearwater Bay survey grid from Shearwater_Bay.kmz (Gaia GPS export,
 *    201 cells, rows A–O), converted to public/maps/shearwater_bay.geojson.
 */

export const GRID_URL = `${import.meta.env.BASE_URL}maps/shearwater_bay.geojson`;
export const EMPTY = { type: 'FeatureCollection', features: [] };
export const GRID_CENTER = [15.1067, -26.6292];
/** Deepest zoom on both maps: ~5–10 m on the scale bar at this latitude. */
export const MAX_ZOOM = 21;

export const SATELLITE = {
  type: 'raster',
  tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
  tileSize: 256,
  // Real imagery at Shearwater Bay stops at z17: Esri answers z18+ with a grey
  // "Map data not yet available" tile. Capping the source here makes MapLibre
  // stretch the z17 image when zooming further instead of showing that tile.
  maxzoom: 17,
  attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community',
};

const SAT_PREF = 'asket.redesign.satellite';
export const readSatPref = () => {
  try { return localStorage.getItem(SAT_PREF) !== 'off'; } catch { return true; }
};
export const writeSatPref = (on) => {
  try { localStorage.setItem(SAT_PREF, on ? 'on' : 'off'); } catch { /* private window */ }
};

/** The grid file, fetched once per page and shared. */
let gridPromise = null;
export function loadGrid() {
  if (!gridPromise) {
    gridPromise = fetch(GRID_URL).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    });
    gridPromise.catch(() => { gridPromise = null; });
  }
  return gridPromise;
}

export function gridBounds(fc) {
  if (!fc?.features?.length) return null;
  const b = new maplibregl.LngLatBounds();
  fc.features.forEach((f) => f.geometry.coordinates[0].forEach((p) => b.extend(p)));
  return b;
}

/**
 * Adds satellite + grid to a loaded map. Call it first in the map's `load`
 * handler so the map's own layers (route, track, vessel) draw above.
 * `onSatError(bool)` reports whether the imagery is failing to load.
 */
export function addAreaLayers(m, grid, { satellite = true, onSatError } = {}) {
  m.addSource('satellite', SATELLITE);
  m.addLayer({
    id: 'satellite', type: 'raster', source: 'satellite',
    layout: { visibility: satellite ? 'visible' : 'none' },
  });
  if (onSatError) {
    m.on('error', (e) => { if (e?.sourceId === 'satellite') onSatError(true); });
    m.on('sourcedata', (e) => {
      if (e.sourceId === 'satellite' && e.tile && e.isSourceLoaded) onSatError(false);
    });
  }
  m.addSource('area-grid', { type: 'geojson', data: grid || EMPTY });
  m.addLayer({
    id: 'area-grid-fill', type: 'fill', source: 'area-grid',
    paint: { 'fill-color': '#79b9cd', 'fill-opacity': 0.06 },
  });
  m.addLayer({
    id: 'area-grid-line', type: 'line', source: 'area-grid',
    paint: { 'line-color': '#79b9cd', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 0.6, 17, 1.6], 'line-opacity': 0.85 },
  });
  m.addLayer({
    id: 'area-grid-label', type: 'symbol', source: 'area-grid', minzoom: 15,
    layout: {
      'text-field': ['concat', ['get', 'row'], '-', ['get', 'col']],
      'text-size': ['interpolate', ['linear'], ['zoom'], 15, 9, 18, 13],
      'text-allow-overlap': false,
    },
    paint: { 'text-color': 'rgba(230,237,243,0.85)', 'text-halo-color': 'rgba(13,17,23,0.8)', 'text-halo-width': 1 },
  });
}

export function setSatelliteVisible(m, on) {
  if (m?.getLayer('satellite')) m.setLayoutProperty('satellite', 'visibility', on ? 'visible' : 'none');
}
