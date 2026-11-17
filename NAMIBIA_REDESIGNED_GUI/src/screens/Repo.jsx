import { useRef, useState } from 'react';

import { bytes, duration } from '@asket/lib/format.js';

import {
  Banner, ConfirmAction, CoursePreview, GlassPanel, Pill, SearchInput, Select,
} from '../components/ui.jsx';
import {
  IconDownload, IconFolder, IconNavigate, IconPlus, IconSend, IconSort, IconTrash, IconUpload,
} from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import {
  ROUTE_FILE_ACCEPT, ROUTE_FILE_TYPES, downloadMission, estimateS, fmtKm, fmtMin, newMission, parseMission,
  routeLengthM, routeValid,
} from '../lib/missionFile.js';
import { preflightVerdict } from '../lib/vessel.js';
import { isRunning, routeOf, routePill } from '../lib/route.js';

/**
 * Repo — two libraries, labelled apart:
 *  - Saved missions: routes stored ON THE GUI SERVER (lib/missionStore.js →
 *    gui_backend /api/routes), .json in/out. In mock mode, this browser.
 *  - Recordings on the vessel: the backend `mission` stream's list of what
 *    has been recorded to the Jetson's disk.
 * "Deploy" sends the server's copy of a route to mission_manager (two-step,
 * confirmed only when /mission/status reports RUNNING). It does not arm the
 * thrusters or change the mode: the boat drives the route only in AUTONOMOUS
 * with the RC arm switch up.
 */
const SORTS = {
  saved: { label: 'Last saved · newest first', fn: (a, b) => (b.saved_utc_ms || 0) - (a.saved_utc_ms || 0) },
  name: { label: 'Name · A–Z', fn: (a, b) => a.name.localeCompare(b.name) },
  distance: { label: 'Distance · shortest first', fn: (a, b) => routeLengthM(a.waypoints) - routeLengthM(b.waypoints) },
};

export function Repo({ v, connection, library, onOpenInDesigner, onLaunch }) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('saved');
  const [error, setError] = useState(null);
  const [pickedId, setPickedId] = useState(null);
  const fileRef = useRef(null);
  const verdict = preflightVerdict(v);
  // Deploy is gated on the pre-flight verdict, as the backend gates it. Not on
  // "armed": arming is the RC transmitter's, and the boat only drives the
  // route once armed and in Autonomous — after deploying is the normal order.
  const ready = verdict.level === 'ok';
  const recordings = v.mission?.missions || [];
  const route = routeOf(v);
  const running = isRunning(route);
  const deployedId = route?.deployed?.route_id;
  const onServer = library.mode === 'server';
  const [busy, setBusy] = useState(false);
  const deployBlock = !onServer ? 'Mock mode: no GUI server or vessel to deploy to.'
    : running ? 'A route is running. Abort it in Navigate first.'
      : !v.state.connected ? 'Not connected to the vessel'
        : !ready ? 'Complete preflight to deploy' : null;

  const list = library.missions
    .filter((m) => m.name.toLowerCase().includes(query.toLowerCase()))
    .sort(SORTS[sort].fn);
  const picked = list.find((m) => m.id === pickedId) || list.find((m) => m.id === library.active?.id) || list[0] || null;

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const result = parseMission(await file.text(), file.name);
    if (!result.mission) { setError(`${file.name}: ${result.errors.join(' ')}`); return; }
    setBusy(true);
    const saved = await library.save({ ...result.mission, file_name: file.name });
    setBusy(false);
    if (!saved.ok) { setError(`${file.name}: ${saved.error}`); return; }
    setError(null);
    setPickedId(saved.mission.id);
  };

  return (
    <>
      <WorkspaceHeader title="Repo">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="repo-title">
          <h2 className="section-title">Saved missions</h2>
          <span className="hint">{onServer ? 'Stored on the GUI server' : 'Stored in this browser (mock mode)'}</span>
          <input ref={fileRef} type="file" accept={ROUTE_FILE_ACCEPT} hidden onChange={onFile} />
          <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}><IconUpload /> {busy ? 'Uploading…' : `Import ${ROUTE_FILE_TYPES}`}</button>
          <button type="button" className="btn primary" onClick={() => onOpenInDesigner(newMission())}><IconPlus /> Create mission</button>
        </div>

        <div className="filters">
          <SearchInput className="repo-search" placeholder="Search by mission name…" value={query} onChange={setQuery} />
          <Select
            icon={<IconSort />} value={sort} onChange={setSort}
            options={Object.entries(SORTS).map(([value, o]) => ({ value, label: o.label }))}
          />
          <span className="count-label" style={{ marginLeft: 'auto' }}>{library.missions.length} saved</span>
        </div>

        {error && <><Banner inline level="alarm" title="Import rejected" detail={error} /><div style={{ height: 12 }} /></>}
        {library.error && <><Banner inline level="alarm" title="GUI server unreachable." detail={`${library.error}. The list below may be out of date.`} actions={<button type="button" className="btn small" onClick={library.refresh}>Retry</button>} /><div style={{ height: 12 }} /></>}
        {library.localOnly.length > 0 && (
          <>
            <Banner
              inline level="info" title={`${library.localOnly.length} mission(s) only in this browser.`}
              detail="Saved before the server library existed."
              actions={<button type="button" className="btn small" onClick={async () => { const errs = await library.uploadLocal(); if (errs.length) setError(errs.join(' · ')); }}><IconUpload /> Upload to server</button>}
            />
            <div style={{ height: 12 }} />
          </>
        )}
        {ready
          ? <Banner inline level="ok" title="Preflight complete." detail="Deploy sends the route to the vessel's mission manager. It does not arm or change mode: the boat drives it only in Autonomous with the arm switch up." />
          : <Banner inline level="warn" title="Review before deploying." detail="Saved routes do not confirm current vessel readiness. Run the preflight; Deploy unlocks on GO." />}
        <div style={{ height: 12 }} />

        <section className="glass panel repo-library">
          {list.length === 0 ? (
            <div className="empty">
              {library.loading ? 'Loading routes from the GUI server…' : library.missions.length === 0
                ? <>No saved missions yet. Import a .json or .csv (an example is at <span className="mono">/missions/walvis_survey_01.json</span>) or create one.</>
                : 'No mission matches the search.'}
            </div>
          ) : (
            <table className="table lines">
              <thead>
                <tr>
                  <th>Course preview</th><th>Mission name / route</th><th>Distance</th><th>Est. execution</th><th>Last saved ↓</th>
                  <th className={`ready-col ${ready ? 'ok' : 'warn'}`}>{ready ? 'Ready to go' : 'Not ready'}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.id} className={picked?.id === m.id ? 'selected' : ''} onClick={() => setPickedId(m.id)}>
                    <td><CoursePreview waypoints={m.waypoints} /></td>
                    <td>
                      <div className="repo-name">
                        {m.name}
                        {picked?.id === m.id && <span className="selected-tag">Selected</span>}
                      </div>
                      <div className="hint">{m.waypoints.length} waypoints · {m.file_name || 'designed on basemap'}</div>
                      {deployedId === m.id && route?.state && (
                        <Pill level={routePill(route).level}>
                          {routePill(route).text}{running ? ` · ${route.current_waypoint}/${route.total_waypoints}` : ''}
                        </Pill>
                      )}
                    </td>
                    <td className="mono">{fmtKm(routeLengthM(m.waypoints))}</td>
                    <td className="mono">{fmtMin(estimateS(m))}<div className="hint">at {m.cruise_speed_ms} m/s</div></td>
                    <td>{m.saved_utc_ms ? new Date(m.saved_utc_ms).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}<div className="hint">{m.stored === 'server' ? 'On the GUI server' : 'This browser'}</div></td>
                    <td className="ready-col" onClick={(e) => e.stopPropagation()}>
                      <ConfirmAction
                        connection={connection} state={v.state} command="deploy_route"
                        args={{ route_id: m.id }}
                        label="Deploy" icon={<IconSend />} variant={`small ${ready ? 'go' : 'warn'}`}
                        prompt={`Deploy ${m.name}?`}
                        disabled={Boolean(deployBlock) || !routeValid(m) || m.stored !== 'server'}
                        title={deployBlock || `Sends ${m.waypoints.length} waypoints to mission_manager.`}
                        resultFor={(latest) => latest.args?.route_id === m.id}
                      />
                      {deployBlock && <div className="launch-note">{deployBlock}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="panel-foot hint">
            Showing {list.length} of {library.missions.length} saved missions · ○ Start / ■ End · schematic courses, not for navigation · deploying does not arm ASKET or change its mode.
          </div>
        </section>

        {picked && (
          <div className="glass repo-dock">
            <strong>{picked.name}</strong>
            <span>·</span><span>{fmtKm(routeLengthM(picked.waypoints))}</span>
            <span>·</span><span>{fmtMin(estimateS(picked))}</span>
            <span style={{ marginLeft: 'auto' }} />
            <button type="button" className="btn ghost" onClick={() => downloadMission(picked)}><IconDownload /> Export .json</button>
            <button
              type="button" className="btn ghost"
              onClick={async () => {
                if (!window.confirm(`Delete ${picked.name}${onServer ? ' from the GUI server' : ''}?`)) return;
                const r = await library.remove(picked.id);
                if (!r.ok) setError(r.error); else setPickedId(null);
              }}
            >
              <IconTrash /> Delete
            </button>
            <button type="button" className="btn ghost" onClick={() => onLaunch(picked)}><IconNavigate /> Show in Navigate</button>
            <button type="button" className="btn primary" onClick={() => onOpenInDesigner(picked)}><IconFolder /> Open in mission designer</button>
          </div>
        )}

        <div style={{ height: 12 }} />
        <GlassPanel title="Recordings on the vessel" aside={<span className="hint">From the vessel’s mission recorder</span>}>
          {recordings.length === 0 ? (
            <div className="empty">{v.state.connected ? 'No recordings on the vessel yet.' : 'Connect to the vessel to list its recordings.'}</div>
          ) : (
            <table className="table">
              <thead><tr><th>Recording</th><th>Duration</th><th>Size</th><th>State</th></tr></thead>
              <tbody>
                {recordings.map((r) => (
                  <tr key={r.path}>
                    <td className="repo-name">{r.name}</td>
                    <td className="mono">{duration(r.duration_s)}</td>
                    <td className="mono">{bytes(r.size_bytes)}</td>
                    <td><Pill level={r.complete ? 'ok' : 'warn'}>{r.complete ? 'Complete' : 'Interrupted'}</Pill></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="hint" style={{ margin: '8px 0 0' }}>Export and delete recordings from the original cockpit’s Recording panel; several GB per hour do not go over the wireless link.</p>
        </GlassPanel>
      </div>
    </>
  );
}
