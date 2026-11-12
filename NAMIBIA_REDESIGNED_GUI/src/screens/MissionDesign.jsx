import { useRef, useState } from 'react';

import { isFitted } from '@asket/lib/fitted.js';

import { GlassPanel, Pill } from '../components/ui.jsx';
import {
  IconDown, IconDownload, IconFileJson, IconFocus, IconLock, IconPencil, IconPin, IconPlus, IconSave,
  IconSend, IconTrash, IconUp, IconUpload,
} from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { PlannerMap } from '../slots/PlannerMap.jsx';
import {
  downloadMission, estimateS, fmtKm, fmtMin, legs, parseMission, routeLengthM, routeValid, validLat, validLon,
} from '../lib/missionFile.js';
import { preflightVerdict } from '../lib/vessel.js';

const STATUS_TEXT = { PASS: 'Verified', WARN: 'Warning', FAIL: 'Failed', SKIPPED: 'Not checked' };
const STATUS_LEVEL = { PASS: 'ok', WARN: 'warn', FAIL: 'alarm', SKIPPED: 'neutral' };

function itemFor(report, prefix) {
  return (report?.items || []).find((i) => i.id.startsWith(prefix));
}

/** Figma: a ticked (required, not editable) box, then the snapshot in colour. */
function HardwareRow({ label, item }) {
  return (
    <div className="config-row">
      <label className="check"><input type="checkbox" checked readOnly disabled />{label}</label>
      <span className={`status-text ${item ? STATUS_LEVEL[item.status] : 'neutral'}`}>
        {item ? STATUS_TEXT[item.status] : 'No report'}
      </span>
    </div>
  );
}

export function MissionDesign({ v, connection, draft, setDraft, library }) {
  const [selected, setSelected] = useState(0);
  const [addMode, setAddMode] = useState(false);
  const [fitKey, setFitKey] = useState(0);
  const [importResult, setImportResult] = useState(null);
  const [savedAt, setSavedAt] = useState(null);
  const fileRef = useRef(null);

  const wps = draft.waypoints;
  const legM = legs(wps);
  const valid = routeValid(draft);
  const verdict = preflightVerdict(v);
  const preflightGo = Boolean(v.report?.go);
  const lidarFitted = isFitted(v.state, 'lidar');
  const cameraFitted = isFitted(v.state, 'camera');

  const update = (patch) => setDraft({ ...draft, ...patch });
  const setWps = (next) => update({ waypoints: next });

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const result = parseMission(await file.text(), file.name);
    setImportResult({ ...result, fileName: file.name });
    if (result.mission) {
      setDraft({ ...result.mission, id: draft.id });
      setSelected(0);
      setFitKey((k) => k + 1);
    }
  };

  const editWp = (i, key, raw) => {
    const value = raw === '' || raw === '-' ? raw : Number(raw);
    setWps(wps.map((w, j) => (j === i ? { ...w, [key]: value } : w)));
  };
  const moveWp = (i, d) => {
    const j = i + d;
    if (j < 0 || j >= wps.length) return;
    const next = [...wps];
    [next[i], next[j]] = [next[j], next[i]];
    setWps(next);
    setSelected(j);
  };
  const deleteWp = (i) => { setWps(wps.filter((_, j) => j !== i)); setSelected(Math.max(0, i - 1)); };
  const addWp = () => {
    const last = wps[wps.length - 1];
    setWps([...wps, last ? { lat: +(last.lat + 0.001).toFixed(6), lon: last.lon } : { lat: -22.9576, lon: 14.5053 }]);
    setSelected(wps.length);
  };

  const save = () => { library.save(draft); setSavedAt(Date.now()); };
  const state = !valid ? 'Draft · unsent' : 'Validated · unsent';

  const sensors = [
    { key: 'sonar', label: 'Sonar · separate SonarView', available: v.sonar?.connected ? 'Available' : 'Not sending data', ok: Boolean(v.sonar?.connected) },
    { key: 'lidar', label: 'Lidar', available: lidarFitted ? 'Fitted' : 'Not fitted', ok: lidarFitted },
    { key: 'camera', label: 'OAK camera', available: cameraFitted ? 'Fitted' : 'Not fitted', ok: cameraFitted },
  ];

  // Figma: a separate Readiness panel under the configuration.
  let readiness;
  if (!valid) {
    readiness = {
      level: 'warn', title: 'Route incomplete', pill: 'Draft',
      ok: null,
      body: 'A mission needs a name and at least two in-range GPS points. Planning, saving and export remain available.',
    };
  } else if (!preflightGo) {
    readiness = {
      level: 'warn', title: 'Send blocked', pill: 'NO-GO',
      ok: 'Route format and coordinates valid for planning',
      body: 'Resolve the preflight first, then re-run it. Saving and export remain available.',
    };
  } else {
    readiness = {
      level: 'ok', title: 'Mission validated', pill: 'Ready to go',
      ok: 'Route, required hardware and preflight verified',
      body: 'Mission validated and not yet sent. Sending does not arm or launch the vessel.',
    };
  }

  return (
    <>
      <WorkspaceHeader title="Mission Design">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="design">
          <div className="design-top">
            <label className="name-field">
              <div className="name-label">Mission name</div>
              <span className="name-edit">
                <input
                  className={`name-input ${draft.name.trim() ? '' : 'invalid'}`}
                  size={Math.max(8, draft.name.length + 1)}
                  value={draft.name} onChange={(e) => update({ name: e.target.value.toUpperCase() })}
                  placeholder="MISSION_NAME"
                />
                <IconPencil />
              </span>
            </label>
            <Pill level={valid ? (preflightGo ? 'ok' : 'info') : 'info'}>{state}</Pill>
            <div className="summary">
              <div className="big">{wps.length} waypoints / {fmtKm(routeLengthM(wps))} / {fmtMin(estimateS(draft))}</div>
              <div className="hint">Open route · estimate at {draft.cruise_speed_ms} m/s · no endurance validation</div>
            </div>
          </div>

          <div className="source-row">
            <div className="glass source-card">
              <IconFileJson />
              <div className="text">
                <strong>Import an existing .json mission</strong>
                {importResult?.mission && (
                  <>
                    <div className="hint">{importResult.fileName} · {importResult.mission.waypoints.length} GPS points loaded</div>
                    <div className="ok-line">✓ Valid JSON · numeric lat/lon in range · WGS 84</div>
                  </>
                )}
                {importResult?.errors?.length > 0 && (
                  <div className="err-line">✕ Rejected {importResult.fileName}: {importResult.errors.slice(0, 2).join(' ')}</div>
                )}
                {importResult?.warnings?.map((w) => <div key={w} className="hint">{w}</div>)}
                {!importResult && <div className="hint">The basemap refits to the imported route.</div>}
              </div>
              <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={onFile} />
              <button type="button" className="btn" onClick={() => fileRef.current?.click()}><IconUpload /> Import .json</button>
            </div>
            <div className={`glass source-card ${addMode ? 'selected' : ''}`}>
              <IconPin />
              <div className="text">
                <strong>Or create a mission on the basemap</strong>
                <div className="hint">Place GPS points in order, then edit the shared route below. Invalid JSON or out-of-range coordinates are rejected, not sent.</div>
              </div>
              <button type="button" className={`btn ${addMode ? 'go' : 'primary'}`} onClick={() => setAddMode((a) => !a)}>
                <IconSend /> {addMode ? 'Done placing' : 'Place GPS points'}
              </button>
            </div>
          </div>

          <div className="design-left">
          <GlassPanel
            title="GPS basemap / Shearwater Bay" flush className="planner-panel"
            aside={<span className="hint">WGS 84 · decimal degrees</span>}
            footer={(
              <div className="planner-bar">
                <span className="place-hint"><IconSend /> {addMode ? `Place points in order · click map to add WP ${wps.length + 1}` : 'Place GPS points to add to the route'}</span>
                <span className="mid">Drag a pin to edit · click a pin to select</span>
                <button type="button" className="btn" onClick={() => setFitKey((k) => k + 1)} disabled={!wps.length}><IconFocus /> Fit route</button>
              </div>
            )}
          >
            <div className="planner" style={{ position: 'relative' }}>
              <PlannerMap
                connection={connection}
                waypoints={wps.filter((w) => validLat(w.lat) && validLon(w.lon))}
                selected={selected}
                onSelect={setSelected}
                addMode={addMode}
                fitKey={fitKey}
                onAdd={(p) => { setWps([...wps, p]); setSelected(wps.length); }}
                onMove={(i, p) => setWps(wps.map((w, j) => (j === i ? p : w)))}
              />
            </div>
          </GlassPanel>

          <GlassPanel
            title={`Waypoints / ${wps.length} points`}
            aside={<span className="hint">{wps.length ? `Selected: WP ${selected + 1} · all fields editable` : ''}</span>}
          >
            <table className="table">
              <thead>
                <tr><th>#</th><th>Latitude (−90…90)</th><th>Longitude (−180…180)</th><th>Leg distance</th><th>Reorder / delete</th></tr>
              </thead>
              <tbody>
                {wps.map((w, i) => (
                  <tr key={i} className={i === selected ? 'selected' : ''} onClick={() => setSelected(i)}>
                    <td><span className="wp-num">{i + 1}</span></td>
                    <td><input className={`input mono ${validLat(w.lat) ? '' : 'invalid'}`} value={w.lat} onChange={(e) => editWp(i, 'lat', e.target.value)} /></td>
                    <td><input className={`input mono ${validLon(w.lon) ? '' : 'invalid'}`} value={w.lon} onChange={(e) => editWp(i, 'lon', e.target.value)} /></td>
                    <td className="mono">{i === 0 ? 'Start' : Number.isFinite(legM[i]) ? `${Math.round(legM[i])} m` : '—'}</td>
                    <td>
                      <span className="btn-row">
                        <button type="button" className="btn small ghost" onClick={() => moveWp(i, -1)} disabled={i === 0} aria-label="Move up"><IconUp /></button>
                        <button type="button" className="btn small ghost" onClick={() => moveWp(i, 1)} disabled={i === wps.length - 1} aria-label="Move down"><IconDown /></button>
                        <button type="button" className="btn small ghost" onClick={() => deleteWp(i)} aria-label="Delete"><IconTrash /></button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button type="button" className="btn" onClick={addWp}><IconPlus /> Add waypoint</button>
          </GlassPanel>

          </div>
          <div className="design-right">
          <GlassPanel title="Mission configuration" className="config-panel" aside={<span className="hint">Applies to either route source</span>}>
            <div className="config-section">
              <div className="config-heads"><span className="upper">Hardware required</span><span className="upper">Snapshot availability</span></div>
              <HardwareRow label="Pico + propulsion / safety chain" item={itemFor(v.report, 'pico')} />
              <HardwareRow label="RC link + hardware killswitch" item={itemFor(v.report, 'rc')} />
              <HardwareRow label="GNSS fix" item={itemFor(v.report, 'gnss')} />
              <HardwareRow label="Heading / IMU" item={itemFor(v.report, 'heading')} />
            </div>
            <div className="config-section">
              <span className="upper">Sensors to use</span>
              {sensors.map((s) => (
                <div className="config-row" key={s.key}>
                  <label className="check">
                    <input
                      type="checkbox" checked={Boolean(draft.sensors[s.key])}
                      onChange={(e) => update({ sensors: { ...draft.sensors, [s.key]: e.target.checked } })}
                    />
                    {s.label}
                  </label>
                  <span className={`status-text ${s.ok ? 'ok' : draft.sensors[s.key] ? 'warn' : 'neutral'}`}>{s.available}</span>
                </div>
              ))}
              <p className="hint">Checked = required by this mission, not proof of readiness.</p>
            </div>
            <div className="config-section speed-row">
              <span>Cruise speed</span>
              <input
                className="input mono" type="number" min="0.1" max="5" step="0.1" style={{ width: 64 }}
                value={draft.cruise_speed_ms} onChange={(e) => update({ cruise_speed_ms: Number(e.target.value) })}
                title="Illustrative planning value"
              />
              <span className="muted">m/s</span>
            </div>
            <div className="config-section">
              <span className="upper">On obstacle detection</span>
              <div className="policy">
                <label className={`raised ${draft.obstacle_policy === 'stop' ? 'selected' : ''}`}>
                  <input type="radio" checked={draft.obstacle_policy === 'stop'} onChange={() => update({ obstacle_policy: 'stop' })} />
                  Stop the vessel
                </label>
                <label
                  className={`raised ${draft.obstacle_policy === 'avoid' ? 'selected' : ''} ${lidarFitted ? '' : 'disabled'}`}
                  title={lidarFitted ? '' : 'Avoidance needs a healthy obstacle detector. No lidar is fitted.'}
                >
                  <input type="radio" disabled={!lidarFitted} checked={draft.obstacle_policy === 'avoid'} onChange={() => update({ obstacle_policy: 'avoid' })} />
                  Avoid the obstacle
                </label>
              </div>
              <p className="hint">
                {lidarFitted
                  ? 'Stop is the safer policy. The policy is saved with the mission; it is not a detection guarantee.'
                  : 'No healthy obstacle detector is confirmed. Stop is a saved policy, not a working detection guarantee.'}
              </p>
            </div>
          </GlassPanel>
          <section className={`glass panel readiness ${readiness.level}`}>
            <header className="panel-header">
              <span>Readiness / {readiness.title}</span>
              <span className="aside"><Pill level={readiness.level}>{readiness.pill}</Pill></span>
            </header>
            <div className="panel-body">
              {readiness.ok && <div className="ok-line">✓ {readiness.ok}</div>}
              <p className={readiness.level === 'ok' ? '' : 'warn-text'}>{readiness.body}</p>
              {!preflightGo && <p className="hint">No endurance clearance: speed and duration are estimates, not vessel capability.</p>}
            </div>
          </section>
          </div>

          <div className="glass design-actions">
            <div className="msg">
              <strong className={valid && preflightGo ? 'ok-text' : ''}>{valid ? (preflightGo ? 'Ready to go. Mission validated · not yet sent.' : 'Planning is available. Deployment is blocked.') : 'Draft — complete the route to validate it.'}</strong>
              <div className="hint">
                Saving, exporting or sending does not arm or launch the vessel.
                {savedAt && ' · Saved to the mission library.'}
              </div>
            </div>
            <button type="button" className="btn primary" onClick={save} disabled={!draft.name.trim()}><IconSave /> Save mission</button>
            <button type="button" className="btn" onClick={() => downloadMission(draft)} disabled={!valid}><IconDownload /> Export .json</button>
            <button
              type="button" className="btn" disabled
              title="Route upload is not wired yet: gui_backend has no command to send waypoints to the vessel (see README)."
            >
              <IconLock /> Send to vessel · not wired
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
