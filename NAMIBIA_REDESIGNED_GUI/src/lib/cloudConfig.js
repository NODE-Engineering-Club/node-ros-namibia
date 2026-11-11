/**
 * Where the cloud build reads Azure from.
 *
 * In order:
 * 1. A viewer link opened once: `https://<site>/#blob=…&container=…&asv=…&sas=…`
 *    (what cloud/azure/provision.py prints). Kept in this browser, then the
 *    fragment is replaced with a normal screen route, so the token does not
 *    stay in the address bar. A fragment is never sent to the web server, so
 *    the token is not in the host's access logs either.
 * 2. Build-time variables (VITE_AZURE_BLOB, VITE_AZURE_CONTAINER,
 *    VITE_AZURE_ASV, VITE_AZURE_SAS). Convenient, but then the token is in
 *    the published JavaScript and anyone who can open the site can read the
 *    telemetry. See cloud/azure/README.md.
 */
const KEY = 'asket.cloud.v1';

function parse(text) {
  const i = text.indexOf('#');
  const p = new URLSearchParams(i >= 0 ? text.slice(i + 1) : text);
  const cfg = {
    blob: p.get('blob'),
    container: p.get('container') || 'telemetry',
    asv: p.get('asv') || 'ASKET_02',
    sas: p.get('sas'),
  };
  return cfg.blob && cfg.sas ? cfg : null;
}

export function cloudConfigFromLink(text) {
  return parse(text.trim());
}

export function saveCloudConfig(cfg) {
  try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch { /* private window */ }
}

export function forgetCloudConfig() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

export function loadCloudConfig() {
  if (window.location.hash.includes('sas=')) {
    const cfg = parse(window.location.hash);
    if (cfg) {
      saveCloudConfig(cfg);
      history.replaceState(null, '', `${window.location.pathname}${window.location.search}#preflight`);
      return { ...cfg, from: 'link' };
    }
  }
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (stored?.blob && stored?.sas) return { ...stored, from: 'browser' };
  } catch { /* ignore */ }
  const env = import.meta.env;
  if (env.VITE_AZURE_BLOB && env.VITE_AZURE_SAS) {
    return {
      blob: env.VITE_AZURE_BLOB,
      container: env.VITE_AZURE_CONTAINER || 'telemetry',
      asv: env.VITE_AZURE_ASV || 'ASKET_02',
      sas: env.VITE_AZURE_SAS.replace(/^\?/, ''),
      from: 'build',
    };
  }
  return null;
}
