import { useRef, useState } from 'react';

import { bytes, duration } from '@asket/lib/format.js';

import { Banner, CoursePreview, GlassPanel, Pill } from '../components/ui.jsx';
import { IconDownload, IconPlus, IconTrash, IconUpload } from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import {
  downloadMission, estimateS, fmtKm, fmtMin, newMission, parseMission, routeLengthM, routeValid,
} from '../lib/missionFile.js';
import { preflightVerdict } from '../lib/vessel.js';

/**
 * Repo — two libraries, labelled apart:
 *  - Saved missions: planned routes (this browser's library, .json in/out).
 *  - Recordings on the vessel: the backend `mission` stream's list of what
 *    has been recorded to the Jetson's disk.
 * "Launch mission" only opens a saved mission in Navigate; arming and launch
 * stay separate operator actions.
 */
export function Repo({ v, library, onOpenInDesigner, onLaunch }) {
  const [query, setQuery] = useState('');
  const [error, setError] = useState(null);
  const fileRef = useRef(null);
  const verdict = preflightVerdict(v);
  const ready = v.ready.ready;
  const recordings = v.mission?.missions || [];

  const list = library.missions.filter((m) => m.name.toLowerCase().includes(query.toLowerCase()));

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const result = parseMission(await file.text(), file.name);
    if (result.mission) { library.save(result.mission); setError(null); } else setError(`${file.name}: ${result.errors.join(' ')}`);
  };

  return (
    <>
      <WorkspaceHeader title="Saved missions" crumb="ASKET / Repo">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="repo-head">
          <input className="input" placeholder="Search by mission name…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <span className="hint">Last saved · newest first</span>
          <span style={{ marginLeft: 'auto' }} />
          <Pill level="neutral" dot={false}>{library.missions.length} saved</Pill>
          <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={onFile} />
          <button type="button" className="btn" onClick={() => fileRef.current?.click()}><IconUpload /> Import .json</button>
          <button type="button" className="btn primary" onClick={() => onOpenInDesigner(newMission())}><IconPlus /> Create mission</button>
        </div>

        {error && <><Banner level="alarm" title="Import rejected" detail={error} /><div style={{ height: 10 }} /></>}
        {ready
          ? <Banner level="ok" title="Ready to go. Preflight complete." detail="Launch mission opens the saved mission in Navigate; arming and launch remain separate operator actions." />
          : <Banner level="warn" title="Review before reuse" detail="Saved routes do not confirm current vessel readiness. Review the plan and re-run preflight before deployment." />}
        <div style={{ height: 12 }} />

        <GlassPanel title="Mission library · review before reuse" aside={<span className="hint">Travel estimate = distance ÷ cruise speed; excludes holds and conditions.</span>}>
          {list.length === 0 ? (
            <div className="empty">
              No saved missions yet. Import a .json (an example is at <span className="mono">/missions/walvis_survey_01.json</span>) or create one.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Course preview</th><th>Mission name / route</th><th>Distance</th><th>Est. execution</th><th>Last saved</th><th /></tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.id} className={library.active?.id === m.id ? 'selected' : ''}>
                    <td><CoursePreview waypoints={m.waypoints} /></td>
                    <td>
                      <div className="repo-name">{m.name} {library.active?.id === m.id && <Pill level="info">Selected</Pill>}</div>
                      <div className="hint">{m.waypoints.length} waypoints · {m.source === 'json' ? 'imported .json' : 'designed on basemap'}</div>
                    </td>
                    <td className="mono">{fmtKm(routeLengthM(m.waypoints))}</td>
                    <td className="mono">{fmtMin(estimateS(m))}<div className="hint">at {m.cruise_speed_ms} m/s</div></td>
                    <td>{m.saved_utc_ms ? new Date(m.saved_utc_ms).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}<div className="hint">Saved locally · .json</div></td>
                    <td>
                      <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
                        <button type="button" className="btn small" onClick={() => onOpenInDesigner(m)}>Open in designer</button>
                        <button type="button" className="btn small ghost" onClick={() => downloadMission(m)} aria-label="Export"><IconDownload /></button>
                        <button type="button" className="btn small ghost" onClick={() => library.remove(m.id)} aria-label="Delete"><IconTrash /></button>
                        <button
                          type="button" className="btn small primary"
                          disabled={!routeValid(m)}
                          title={ready ? 'Opens in Navigate. Does not arm or launch.' : 'Opens in Navigate for review. Complete preflight before launch.'}
                          onClick={() => onLaunch(m)}
                        >
                          {ready ? 'Launch mission' : 'Open in Navigate'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="hint" style={{ margin: '8px 0 0' }}>○ Start / ■ End · schematic courses · not for navigation. Opening does not arm or launch ASKET.</p>
        </GlassPanel>

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
