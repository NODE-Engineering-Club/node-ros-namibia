import { useMemo, useState } from 'react';

import { useAlarmSound } from '@asket/lib/alarmSound.js';

import { Banner, CHECK_PILL, GlassPanel, Pill, StatusIcon } from '../components/ui.jsx';
import { IconNavigate, IconPreflight } from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { preflightVerdict } from '../lib/vessel.js';

/**
 * Current alerts — the backend's active alarm list (`alarms` message), the
 * same list the original cockpit's AlarmPanel shows, plus the sound rules
 * from lib/alarmSound.js (red beeps, amber never does). Alert rules are the
 * ones proposed in KELP_MISSIONS.md §4.6 and implemented in gui_backend.
 */

const SEVERITY = { alarm: { level: 'alarm', text: 'Alarm' }, warn: { level: 'warn', text: 'Warning' } };

/** "sonar.dropout" → "sonar". The subsystem is the key's first segment. */
function subsystem(alarm) {
  return String(alarm.key || '').split(/[._:]/)[0] || 'general';
}

export function Alerts({ v, onNavigate }) {
  const { alarms, report } = v;
  const sound = useAlarmSound(alarms);
  const [query, setQuery] = useState('');
  const [severity, setSeverity] = useState('all');
  const [selectedKey, setSelectedKey] = useState(null);
  const verdict = preflightVerdict(v);

  const filtered = useMemo(() => alarms.filter((a) => {
    if (severity !== 'all' && a.severity !== severity) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return [a.message, a.remedy, a.key].some((t) => String(t || '').toLowerCase().includes(q));
  }), [alarms, severity, query]);

  const selected = filtered.find((a) => a.key === selectedKey) || filtered[0] || null;
  const subsystems = [...new Set(alarms.map(subsystem))];
  const red = alarms.filter((a) => a.severity === 'alarm').length;
  const related = selected
    ? (report?.items || []).filter((i) => i.id.split('.')[0] === subsystem(selected) && i.status !== 'PASS')
    : [];

  return (
    <>
      <WorkspaceHeader title="Alerts" crumb="ASKET / Vessel review">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="summary-tiles">
          <div className="glass summary-tile"><div className="n">{alarms.length}</div><div className="t">Current alerts</div><div className="s">Separate issues · all unresolved</div></div>
          <div className="glass summary-tile"><div className="n">{red || alarms.length}</div><div className="t">{red ? 'Alarms' : 'Warnings'}</div><div className="s">{red ? `${alarms.length - red} warnings` : 'No red alarms'}</div></div>
          <div className="glass summary-tile"><div className="n">{subsystems.length}</div><div className="t">Affected subsystems</div><div className="s">{subsystems.join(' · ') || '—'}</div></div>
          <div className="glass summary-tile">
            <div className="n" style={{ color: report?.go ? 'var(--ok)' : 'var(--warn)' }}>{report ? (report.go ? 'GO' : 'NO-GO') : '—'}</div>
            <div className="t">Preflight readiness</div><div className="s">{report ? report.summary : 'No pre-flight report'}</div>
          </div>
        </div>

        <div className="filters">
          <input className="input" placeholder="Search issue, component or subsystem…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select className="input" value={severity} onChange={(e) => setSeverity(e.target.value)} style={{ width: 160 }}>
            <option value="all">Severity: All</option>
            <option value="alarm">Alarms</option>
            <option value="warn">Warnings</option>
          </select>
          {sound.enabled
            ? <button type="button" className="btn" onClick={sound.disable}>Sound off</button>
            : <button type="button" className="btn" onClick={sound.enable} title="Red alarms beep. Amber ones never do.">Enable sound</button>}
          {sound.enabled && red > 0 && <button type="button" className="btn" onClick={sound.silence} disabled={!sound.sounding}>Silence 5 min</button>}
        </div>

        {alarms.length === 0 ? (
          <Banner level="ok" title="No current alerts" detail="Nothing needs attention. Review the preflight before launch — alert review does not authorize it." />
        ) : (
          <div className="alerts-layout">
            <div>
              <Banner level={red ? 'alarm' : 'warn'} title={report?.go ? 'Review the active alerts' : 'NO-GO — readiness unresolved'} detail="Review these issues alongside the full preflight checklist. Alert review does not authorize launch." />
              <div style={{ height: 10 }} />
              {filtered.map((a) => (
                <div
                  key={a.key} role="button" tabIndex={0}
                  className={`glass alert-card ${selected?.key === a.key ? 'selected' : ''}`}
                  onClick={() => setSelectedKey(a.key)} onKeyDown={(e) => e.key === 'Enter' && setSelectedKey(a.key)}
                >
                  <div className="title">
                    {a.message}
                    <Pill level={SEVERITY[a.severity]?.level || 'warn'}>{SEVERITY[a.severity]?.text || a.severity}</Pill>
                    <Pill level="neutral" dot={false}>Unresolved</Pill>
                  </div>
                  <div className="hint">Subsystem: {subsystem(a)}</div>
                  {a.remedy && <div className="hint">Next check: {a.remedy}</div>}
                </div>
              ))}
              {filtered.length === 0 && <div className="empty">No alert matches the filter.</div>}
            </div>

            <GlassPanel title="Alert details" aside={selected && <Pill level={SEVERITY[selected.severity]?.level || 'warn'}>{SEVERITY[selected.severity]?.text}</Pill>}>
              {selected ? (
                <>
                  <h3 style={{ margin: '0 0 12px', fontSize: 18, fontWeight: 500 }}>{selected.message}</h3>
                  <dl className="kv">
                    <dt>Affected subsystem</dt><dd>{subsystem(selected)}</dd>
                    <dt>Alert key</dt><dd className="mono">{selected.key}</dd>
                  </dl>
                  <hr className="divider" />
                  <div className="upper faint" style={{ marginBottom: 6 }}>Suggested next check</div>
                  <p style={{ marginTop: 0 }}>{selected.remedy || 'No remedy supplied by the backend for this alert.'}</p>
                  {related.length > 0 && (
                    <>
                      <div className="upper faint" style={{ margin: '14px 0 6px' }}>Related preflight checks</div>
                      {related.map((i) => (
                        <div key={i.id} className="check-row raised" style={{ marginBottom: 6 }}>
                          <StatusIcon status={i.status} />
                          <div><div className="name">{i.name}</div><div className="msg">{i.message}</div></div>
                          <Pill level={CHECK_PILL[i.status]?.level}>{CHECK_PILL[i.status]?.text}</Pill>
                        </div>
                      ))}
                    </>
                  )}
                  <hr className="divider" />
                  <Banner level="info" title="Review is not resolution" detail="Acknowledging an alert does not repair the issue or clear preflight readiness." />
                </>
              ) : <div className="empty">Select an alert.</div>}
            </GlassPanel>
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 14, justifyContent: 'flex-end' }}>
          <span className="hint" style={{ marginRight: 'auto' }}>Showing {filtered.length} of {alarms.length} current alerts · alert counts are separate from preflight checkpoint counts.</span>
          <button type="button" className="btn" onClick={() => onNavigate('navigate')}><IconNavigate /> Return to navigation</button>
          <button type="button" className="btn primary" onClick={() => onNavigate('preflight')}><IconPreflight /> Review preflight</button>
        </div>
      </div>
    </>
  );
}
