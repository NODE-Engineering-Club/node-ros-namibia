import { useRef, useState } from 'react';

import { bytes, duration } from '@asket/lib/format.js';

import {
  Banner, CoursePreview, GlassPanel, Pill, SearchInput, Select,
} from '../components/ui.jsx';
import {
  IconDownload, IconFolder, IconPlus, IconSort, IconTrash, IconUpload,
} from '../components/Icons.jsx';
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
const SORTS = {
  saved: { label: 'Last saved · newest first', fn: (a, b) => (b.saved_utc_ms || 0) - (a.saved_utc_ms || 0) },
  name: { label: 'Name · A–Z', fn: (a, b) => a.name.localeCompare(b.name) },
  distance: { label: 'Distance · shortest first', fn: (a, b) => routeLengthM(a.waypoints) - routeLengthM(b.waypoints) },
};

export function Repo({ v, library, onOpenInDesigner, onLaunch }) {
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('saved');
  const [error, setError] = useState(null);
  const [pickedId, setPickedId] = useState(null);
  const fileRef = useRef(null);
  const verdict = preflightVerdict(v);
  const ready = v.ready.ready;
  const recordings = v.mission?.missions || [];

  const list = library.missions
    .filter((m) => m.name.toLowerCase().includes(query.toLowerCase()))
    .sort(SORTS[sort].fn);
  const picked = list.find((m) => m.id === pickedId) || list.find((m) => m.id === library.active?.id) || list[0] || null;

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const result = parseMission(await file.text(), file.name);
    if (result.mission) { library.save(result.mission); setError(null); } else setError(`${file.name}: ${result.errors.join(' ')}`);
  };

  return (
    <>
      <WorkspaceHeader title="Repo">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="repo-title">
          <h2 className="section-title">Saved missions</h2>
          <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={onFile} />
          <button type="button" className="btn" onClick={() => fileRef.current?.click()}><IconUpload /> Import .json</button>
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
        {ready
          ? <Banner inline level="ok" title="Ready to go." detail="Preflight complete. Launch mission opens the saved mission in Navigate for launch; arming and launch remain separate operator actions." />
          : <Banner inline level="warn" title="Review before reuse." detail="Saved routes do not confirm current vessel readiness. Review the plan and re-run preflight before deployment." />}
        <div style={{ height: 12 }} />

        <section className="glass panel repo-library">
          {list.length === 0 ? (
            <div className="empty">
              {library.missions.length === 0
                ? <>No saved missions yet. Import a .json (an example is at <span className="mono">/missions/walvis_survey_01.json</span>) or create one.</>
                : 'No mission matches the search.'}
            </div>
          ) : (
            <table className="table lines">
              <thead>
                <tr>
                  <th>Course preview</th><th>Mission name / route</th><th>Distance</th><th>Est. execution</th><th>Last saved ↓</th>
                  <th className={`ready-col ${ready ? 'ok' : 'warn'}`}>{ready ? 'Ready to go' : 'NO-GO'}</th>
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
                      <div className="hint">{m.waypoints.length} waypoints · {m.source === 'json' ? 'imported .json' : 'designed on basemap'}</div>
                    </td>
                    <td className="mono">{fmtKm(routeLengthM(m.waypoints))}</td>
                    <td className="mono">{fmtMin(estimateS(m))}<div className="hint">at {m.cruise_speed_ms} m/s</div></td>
                    <td>{m.saved_utc_ms ? new Date(m.saved_utc_ms).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}<div className="hint">Saved locally · .json</div></td>
                    <td className="ready-col">
                      <button
                        type="button" className={`btn small ${ready ? 'go' : 'warn'}`}
                        disabled={!routeValid(m)}
                        title={ready ? 'Opens in Navigate. Does not arm or launch.' : 'Opens in Navigate for review. Complete preflight before launch.'}
                        onClick={(e) => { e.stopPropagation(); onLaunch(m); }}
                      >
                        Launch mission
                      </button>
                      {!ready && <div className="launch-note">Complete preflight to launch</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="panel-foot hint">
            Showing {list.length} of {library.missions.length} saved missions · ○ Start / ■ End · schematic courses, not for navigation · opening does not arm or launch ASKET.
          </div>
        </section>

        {picked && (
          <div className="glass repo-dock">
            <strong>{picked.name}</strong>
            <span>·</span><span>{fmtKm(routeLengthM(picked.waypoints))}</span>
            <span>·</span><span>{fmtMin(estimateS(picked))}</span>
            <span style={{ marginLeft: 'auto' }} />
            <button type="button" className="btn ghost" onClick={() => downloadMission(picked)}><IconDownload /> Export .json</button>
            <button type="button" className="btn ghost" onClick={() => { library.remove(picked.id); setPickedId(null); }}><IconTrash /> Delete</button>
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
