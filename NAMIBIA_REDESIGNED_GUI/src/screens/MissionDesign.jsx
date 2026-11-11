import { useRef, useState } from 'react';

import { isFitted } from '@asket/lib/fitted.js';

import { Banner, GlassPanel, Pill } from '../components/ui.jsx';
import {
  IconDown, IconDownload, IconMap, IconPin, IconPlus, IconSave, IconSend, IconTrash, IconUp, IconUpload,
} from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { PlannerMap } from '../slots/PlannerMap.jsx';
import {
  downloadMission, estimateS, fmtKm, fmtMin, legs, parseMission, routeLengthM, routeValid, validLat, validLon,
} from '../lib/missionFile.js';
import { preflightVerdict } from '../lib/vessel.js';

const STATUS_TEXT = { PASS: 'verified', WARN: 'warning', FAIL: 'failed', SKIPPED: 'not checked' };
const STATUS_LEVEL = { PASS: 'ok', WARN: 'warn', FAIL: 'alarm', SKIPPED: 'neutral' };

function itemFor(report, prefix) {
  return (report?.items || []).find((i) => i.id.startsWith(prefix));
}

function HardwareRow({ label, item }) {
  return (
    <div className="config-row">
      <span>{label}</span>
      {item
        ? <Pill level={STATUS_LEVEL[item.status]}>{STATUS_TEXT[item.status]}</Pill>
        : <Pill level="neutral">no report</Pill>}
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

  let readiness;
  if (!valid) {
    readiness = <Banner level="warn" title="Route incomplete" detail="A mission needs a name and at least two in-range GPS points. Planning, saving and export remain available." />;
  } else if (!preflightGo) {
    readiness = <Banner level="warn" title="Send blocked — NO-GO" detail="Route format and coordinates are valid for planning. Resolve the preflight first; saving and export remain available." />;
  } else {
    readiness = <Banner level="ok" title="Ready to go" detail="Route, required hardware and preflight verified. Mission validated and not yet sent. Sending does not arm or launch the vessel." />;
  }

  return (
    <>
      <WorkspaceHeader title="Mission Design" crumb="ASKET / Autonomy">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="design">
          <div className="design-top">
            <label>
              <div className="upper faint" style={{ marginBottom: 4 }}>Mission name</div>
              <input
                className={`input ${draft.name.trim() ? '' : 'invalid'}`} style={{ width: 320, fontSize: 16 }}
                value={draft.name} onChange={(e) => update({ name: e.target.value.toUpperCase() })}
              />
            </label>
            <Pill level={valid ? 'info' : 'neutral'}>{state}</Pill>
            <div className="summary">
              <div className="big">{wps.length} waypoints / {fmtKm(routeLengthM(wps))} / {fmtMin(estimateS(draft))}</div>
              <div className="hint">Open route · estimate at {draft.cruise_speed_ms} m/s · no endurance validation</div>
            </div>
          </div>

          <div className="source-row">
            <div className="glass source-card">
              <IconUpload />
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
              <button type="button" className={`btn ${addMode ? 'primary' : 'info'}`} onClick={() => setAddMode((a) => !a)}>
                <IconPin /> {addMode ? 'Done placing' : 'Place GPS points'}
              </button>
            </div>
          </div>

          <GlassPanel
            title="GPS basemap" icon={<IconMap />} flush className="planner-panel"
            aside={<span className="hint">WGS 84 · decimal degrees</span>}
            footer={(
              <div className="planner-bar">
                <span>{addMode ? `Click the map to add WP ${wps.length + 1}` : 'Drag a pin to edit · click a pin to select'}</span>
                <span style={{ marginLeft: 'auto' }} />
                <button type="button" className="btn small" onClick={() => setFitKey((k) => k + 1)} disabled={!wps.length}>Fit route</button>
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

          <GlassPanel title="Mission configuration" className="config-panel">
            <div className="config-section">
              <span className="upper">Hardware required · snapshot</span>
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
                  <Pill level={s.ok ? 'ok' : 'neutral'}>{s.available}</Pill>
                </div>
              ))}
              <p className="hint">Checked = required by this mission, not proof of readiness.</p>
            </div>
            <div className="config-section">
              <span className="upper">Cruise speed</span>
              <div className="btn-row">
                <input
                  className="input" type="number" min="0.1" max="5" step="0.1" style={{ width: 90 }}
                  value={draft.cruise_speed_ms} onChange={(e) => update({ cruise_speed_ms: Number(e.target.value) })}
                />
                <span className="muted">m/s · illustrative planning value</span>
              </div>
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
            {readiness}
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
                    <td><input className={`input ${validLat(w.lat) ? '' : 'invalid'}`} value={w.lat} onChange={(e) => editWp(i, 'lat', e.target.value)} /></td>
                    <td><input className={`input ${validLon(w.lon) ? '' : 'invalid'}`} value={w.lon} onChange={(e) => editWp(i, 'lon', e.target.value)} /></td>
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
            <button type="button" className="btn small" onClick={addWp}><IconPlus /> Add waypoint</button>
          </GlassPanel>

          <div className="glass design-actions">
            <div className="msg">
              <strong>{valid ? (preflightGo ? 'Ready to go. Mission validated · not yet sent.' : 'Planning is available. Deployment is blocked.') : 'Draft — complete the route to validate it.'}</strong>
              <div className="hint">
                Saving, exporting or sending does not arm or launch the vessel.
                {savedAt && ' · Saved to the mission library.'}
              </div>
            </div>
            <button type="button" className="btn" onClick={save} disabled={!draft.name.trim()}><IconSave /> Save mission</button>
            <button type="button" className="btn" onClick={() => downloadMission(draft)} disabled={!valid}><IconDownload /> Export .json</button>
            <button
              type="button" className="btn primary" disabled
              title="Route upload is not wired yet: gui_backend has no command to send waypoints to the vessel (see README)."
            >
              <IconSend /> Send to vessel
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
