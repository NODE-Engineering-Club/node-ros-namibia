/**
 * Mission files: parse, validate, measure and export.
 *
 * A route is an ordered list of WGS 84 points in decimal degrees. The rule the
 * Mission Design screen states — "invalid JSON or out-of-range coordinates are
 * rejected, not sent" — is enforced here, in one pure module, so it can be
 * tested without a browser.
 *
 * Accepted inputs (the first one is what Export writes):
 *   { "format": "asket-mission", "name", "cruise_speed_ms", "waypoints": [{ "lat", "lon" }] }
 *   { "waypoints": [[lat, lon], ...] }          — pairs are lat, lon
 *   [ { "lat", "lon" }, ... ]                    — a bare array
 *   GeoJSON LineString / FeatureCollection       — coordinates are lon, lat
 *   CSV, one waypoint per row                    — see parseCsvPoints()
 */

/** File types the import buttons and drop zones accept. */
export const ROUTE_FILE_ACCEPT = '.json,.csv,application/json,text/csv';
export const ROUTE_FILE_TYPES = '.json / .csv';

export const DEFAULT_CRUISE_MS = 1.5;
export const FORMAT = 'asket-mission';

const EARTH_R = 6371008.8;

export function newMission(name = 'NEW_MISSION') {
  return {
    id: `m-${Date.now().toString(36)}`,
    name,
    cruise_speed_ms: DEFAULT_CRUISE_MS,
    obstacle_policy: 'stop',
    sensors: { sonar: false, lidar: false, camera: false },
    waypoints: [],
    source: 'basemap',
  };
}

export function validLat(v) { return Number.isFinite(v) && v >= -90 && v <= 90; }
export function validLon(v) { return Number.isFinite(v) && v >= -180 && v <= 180; }

function point(raw, order, index, errors) {
  let lat;
  let lon;
  if (Array.isArray(raw)) {
    [lat, lon] = order === 'lonlat' ? [raw[1], raw[0]] : [raw[0], raw[1]];
  } else if (raw && typeof raw === 'object') {
    lat = raw.lat ?? raw.latitude;
    lon = raw.lon ?? raw.lng ?? raw.longitude;
  }
  if (typeof lat !== 'number' || typeof lon !== 'number') {
    errors.push(`Point ${index + 1}: latitude and longitude must be numbers.`);
    return null;
  }
  if (!validLat(lat)) errors.push(`Point ${index + 1}: latitude ${lat} is outside −90…90.`);
  if (!validLon(lon)) errors.push(`Point ${index + 1}: longitude ${lon} is outside −180…180.`);
  return { lat, lon };
}

function geojsonCoords(doc) {
  if (doc.type === 'LineString') return doc.coordinates;
  if (doc.type === 'Feature') return geojsonCoords(doc.geometry || {});
  if (doc.type === 'FeatureCollection') {
    const line = (doc.features || []).find((f) => f.geometry?.type === 'LineString');
    if (line) return line.geometry.coordinates;
    return (doc.features || [])
      .filter((f) => f.geometry?.type === 'Point')
      .map((f) => f.geometry.coordinates);
  }
  return null;
}

const LAT_HEAD = /^(lat|latitude|lat_deg|y)$/i;
const LON_HEAD = /^(lon|lng|long|longitude|lon_deg|x)$/i;

/**
 * CSV: one waypoint per row, in route order. Comma, semicolon or tab
 * separated. With a header row, the columns named lat/latitude and
 * lon/lng/longitude are used, in any position (other columns are ignored);
 * without one, the first two columns are latitude, longitude. Semicolon files
 * may use a decimal comma. Blank lines and lines starting with # are skipped.
 * Every row is checked; any bad row rejects the file, with its line number.
 */
function parseCsvPoints(text) {
  const errors = [];
  const rows = text.replace(/^\uFEFF/, '').split(/\r?\n/)
    .map((line, i) => ({ line, n: i + 1 }))
    .filter(({ line }) => line.trim() && !line.trim().startsWith('#'));
  if (!rows.length) return { raw: null, errors: ['The CSV file is empty.'] };
  const first = rows[0].line;
  const delim = first.includes('\t') ? '\t' : first.includes(';') ? ';' : ',';
  const split = (line) => line.split(delim).map((c) => c.trim().replace(/^"(.*)"$/, '$1'));
  const num = (c) => {
    const t = delim === ';' ? c.replace(',', '.') : c;
    return t === '' ? NaN : Number(t);
  };

  let latCol = 0;
  let lonCol = 1;
  let body = rows;
  const head = split(first);
  if (head.some((c) => c !== '' && !Number.isFinite(num(c)))) {
    latCol = head.findIndex((c) => LAT_HEAD.test(c));
    lonCol = head.findIndex((c) => LON_HEAD.test(c));
    if (latCol < 0 || lonCol < 0) {
      return {
        raw: null,
        errors: [`CSV header needs a "lat" and a "lon" column (found: ${head.join(', ')}).`],
      };
    }
    body = rows.slice(1);
  }
  const raw = [];
  for (const { line, n } of body) {
    const cells = split(line);
    const lat = num(cells[latCol] ?? '');
    const lon = num(cells[lonCol] ?? '');
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      errors.push(`Line ${n}: latitude and longitude must be numbers.`);
    } else {
      raw.push({ lat, lon });
    }
  }
  return { raw, errors };
}

const isCsv = (text, fileName) => /\.csv$/i.test(fileName)
  || (!/\.json$/i.test(fileName) && !/^\s*[[{]/.test(text));

/**
 * A .json or .csv route file into a mission.
 * @returns {{ mission: object|null, errors: string[], warnings: string[] }}
 */
export function parseMission(text, fileName = '') {
  const errors = [];
  const warnings = [];
  let doc;
  if (isCsv(text, fileName)) {
    const csv = parseCsvPoints(text);
    if (!csv.raw || csv.errors.length) return { mission: null, errors: csv.errors, warnings };
    doc = { waypoints: csv.raw };
  } else {
    try {
      doc = JSON.parse(text);
    } catch (e) {
      return { mission: null, errors: [`Not valid JSON: ${e.message}`], warnings };
    }
  }

  let raw = null;
  let order = 'latlon';
  if (Array.isArray(doc)) raw = doc;
  else if (doc && Array.isArray(doc.waypoints)) raw = doc.waypoints;
  else if (doc && typeof doc.type === 'string') {
    raw = geojsonCoords(doc);
    order = 'lonlat';
  }
  if (!raw) {
    return { mission: null, errors: ['No waypoints found (expected "waypoints", an array or GeoJSON).'], warnings };
  }

  const waypoints = raw.map((r, i) => point(r, order, i, errors)).filter(Boolean);
  if (waypoints.length === 0) errors.push('The file contains no points.');
  else if (waypoints.length === 1) warnings.push('Only one point: add at least one more to make a route.');
  if (errors.length) return { mission: null, errors, warnings };

  const base = newMission();
  const name = (doc.name || fileName.replace(/\.(json|csv)$/i, '') || base.name).toString().toUpperCase();
  const speed = Number(doc.cruise_speed_ms);
  return {
    mission: {
      ...base,
      name,
      cruise_speed_ms: Number.isFinite(speed) && speed > 0 ? speed : DEFAULT_CRUISE_MS,
      obstacle_policy: doc.obstacle_policy === 'avoid' ? 'avoid' : 'stop',
      sensors: { ...base.sensors, ...(doc.sensors || {}) },
      waypoints,
      source: /\.csv$/i.test(fileName) ? 'csv' : 'json',
      file_name: fileName,
    },
    errors,
    warnings,
  };
}

export function toExport(mission) {
  return {
    format: FORMAT,
    version: 1,
    name: mission.name,
    crs: 'WGS84',
    cruise_speed_ms: mission.cruise_speed_ms,
    obstacle_policy: mission.obstacle_policy,
    sensors: mission.sensors,
    waypoints: mission.waypoints.map(({ lat, lon }) => ({ lat, lon })),
  };
}

export function downloadMission(mission) {
  const blob = new Blob([JSON.stringify(toExport(mission), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${mission.name.toLowerCase()}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// -- geometry -------------------------------------------------------------

export function distanceM(a, b) {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.sqrt(h));
}

/** Initial great-circle bearing from a to b, degrees clockwise from north. */
export function bearingDeg(a, b) {
  const rad = Math.PI / 180;
  const y = Math.sin((b.lon - a.lon) * rad) * Math.cos(b.lat * rad);
  const x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad)
    - Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos((b.lon - a.lon) * rad);
  return ((Math.atan2(y, x) / rad) + 360) % 360;
}

export function legs(waypoints) {
  return waypoints.map((w, i) => (i === 0 ? 0 : distanceM(waypoints[i - 1], w)));
}

export function routeLengthM(waypoints) {
  return legs(waypoints).reduce((s, d) => s + d, 0);
}

/** Travel estimate = distance ÷ cruise speed. Excludes holds and conditions. */
export function estimateS(mission) {
  const speed = mission.cruise_speed_ms > 0 ? mission.cruise_speed_ms : DEFAULT_CRUISE_MS;
  return routeLengthM(mission.waypoints) / speed;
}

export function routeValid(mission) {
  return (
    mission.waypoints.length >= 2
    && mission.waypoints.every((w) => validLat(w.lat) && validLon(w.lon))
    && mission.name.trim().length > 0
  );
}

export function fmtKm(m) { return `${(m / 1000).toFixed(2)} km`; }
export function fmtMin(s) { return `~${(s / 60).toFixed(1)} min`; }
