import { useMemo, useState } from 'react';

import { useAlarmSound } from '@asket/lib/alarmSound.js';

import {
  Banner, CHECK_PILL, GlassPanel, Pill, SearchInput, Select, StatusIcon,
} from '../components/ui.jsx';
import {
  IconChevronRight, IconNavigate, IconPreflight, IconUnknown, IconWarn, IconX,
} from '../components/Icons.jsx';
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
  const [system, setSystem] = useState('all');
  const [selectedKey, setSelectedKey] = useState(null);
  const verdict = preflightVerdict(v);

  const filtered = useMemo(() => alarms.filter((a) => {
    if (severity !== 'all' && a.severity !== severity) return false;
    if (system !== 'all' && subsystem(a) !== system) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return [a.message, a.remedy, a.key].some((t) => String(t || '').toLowerCase().includes(q));
  }), [alarms, severity, system, query]);

  const selected = filtered.find((a) => a.key === selectedKey) || filtered[0] || null;
  const subsystems = [...new Set(alarms.map(subsystem))];
  const red = alarms.filter((a) => a.severity === 'alarm').length;
  const amber = alarms.length - red;
  const related = selected
    ? (report?.items || []).filter((i) => i.id.split('.')[0] === subsystem(selected) && i.status !== 'PASS')
    : [];
  const go = Boolean(report?.go);

  return (
    <>
      <WorkspaceHeader title="Alerts">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <h2 className="section-title">Current alerts</h2>

        <div className="summary-tiles">
          <div className="glass summary-tile">
            <div className="n">{alarms.length}</div>
            <div><div className="t">Current alerts</div><div className="s">Separate issues · all unresolved</div></div>
          </div>
          <div className="glass summary-tile">
            <div className="n" style={{ color: red ? 'var(--alarm)' : amber ? 'var(--warn)' : undefined }}>{red || amber}</div>
            <div>
              <div className="t">{red ? 'Alarm severity' : 'Warning severity'}</div>
              <div className="s">{red ? `${red} alarm${red === 1 ? '' : 's'} · ${amber} warning${amber === 1 ? '' : 's'}` : amber ? `All ${amber} reported as warnings` : 'No red alarms'}</div>
            </div>
          </div>
          <div className="glass summary-tile">
            <div className="n">{subsystems.length}</div>
            <div><div className="t">Affected subsystems</div><div className="s">{subsystems.join(' · ') || '—'}</div></div>
          </div>
          <div className="glass summary-tile">
            <div className="n" style={{ color: report ? (go ? 'var(--ok)' : 'var(--warn)') : undefined }}>{report ? (go ? 'GO' : 'NO-GO') : '—'}</div>
            <div>
              <div className="t">Preflight readiness</div>
              <div className="s" title={report?.summary}>{report ? (go ? 'Checks passed · arming is separate' : 'Unresolved checks · launch not permitted') : 'No pre-flight report'}</div>
            </div>
          </div>
        </div>

        <div className="filters">
          <SearchInput className="grow" placeholder="Search issue, component or subsystem…" value={query} onChange={setQuery} />
          <Select
            label="Severity" value={severity} onChange={setSeverity}
            options={[{ value: 'all', label: 'All' }, { value: 'alarm', label: 'Alarms' }, { value: 'warn', label: 'Warnings' }]}
          />
          <Select
            label="Subsystem" value={system} onChange={setSystem}
            options={[{ value: 'all', label: 'All' }, ...subsystems.map((x) => ({ value: x, label: x }))]}
          />
          {sound.enabled
            ? <button type="button" className="btn" onClick={sound.disable}>Sound off</button>
            : <button type="button" className="btn" onClick={sound.enable} title="Red alarms beep. Amber ones never do.">Enable sound</button>}
          {sound.enabled && red > 0 && <button type="button" className="btn" onClick={sound.silence} disabled={!sound.sounding}>Silence 5 min</button>}
          <span className="count-label" style={{ marginLeft: 'auto' }}>{filtered.length} of {alarms.length} current alerts</span>
        </div>

        {alarms.length === 0 ? (
          <Banner inline level="ok" title="No current alerts" detail="Nothing needs attention. Review the preflight before launch — alert review does not authorize it." />
        ) : (
          <>
            <Banner
              inline level={red ? 'alarm' : 'warn'}
              title={!report ? 'Preflight not run' : go ? 'Review the active alerts' : 'NO-GO — readiness unresolved'}
              detail="Review these issues alongside the full preflight checklist. Alert review does not authorize launch."
            />
            <div className="alerts-layout">
              <GlassPanel
                title="Active alerts" className="alerts-list" bodyClass="flush-list"
                aside={<span className="hint">ASKET · {alarms.length} current issue{alarms.length === 1 ? '' : 's'}</span>}
                footer={(
                  <div className="panel-foot hint">
                    Showing {filtered.length} of {alarms.length} current alerts · {red} alarm{red === 1 ? '' : 's'} · {amber} warning{amber === 1 ? '' : 's'} · all unresolved
                    <br />Alert counts are separate from preflight checkpoint counts.
                  </div>
                )}
              >
                {filtered.map((a) => {
                  const sev = SEVERITY[a.severity] || SEVERITY.warn;
                  const isSel = selected?.key === a.key;
                  return (
                    <div
                      key={a.key} role="button" tabIndex={0}
                      className={`alert-row ${isSel ? 'selected' : ''}`}
                      onClick={() => setSelectedKey(a.key)} onKeyDown={(e) => e.key === 'Enter' && setSelectedKey(a.key)}
                    >
                      <div className="title">
                        <span className={`sev-icon ${sev.level}`}>{a.severity === 'alarm' ? <IconX /> : <IconWarn />}</span>
                        <span className="text">{a.message}</span>
                        <Pill level={sev.level}>{sev.text}</Pill>
                        <Pill level="neutral">Unresolved</Pill>
                      </div>
                      <div className="alert-facts">
                        <div><div className="k">Component / subsystem</div><div className="val">{subsystem(a)}</div></div>
                        <div><div className="k">Where on vessel</div><div className="val">Not reported</div></div>
                      </div>
                      {a.remedy && <div className="hint">Next check: {a.remedy}</div>}
                      <span className="row-link">{isSel ? 'Selected' : 'View details'} <IconChevronRight /></span>
                    </div>
                  );
                })}
                {filtered.length === 0 && <div className="empty">No alert matches the filter.</div>}
              </GlassPanel>

              <GlassPanel
                title="Alert details" className="alert-details"
                aside={selected && <span className="info-text">{subsystem(selected).toUpperCase()}</span>}
              >
                {selected ? (
                  <>
                    <h3 className="detail-title">{selected.message}</h3>
                    <div className="btn-row" style={{ marginBottom: 14 }}>
                      <Pill level={SEVERITY[selected.severity]?.level || 'warn'}>{SEVERITY[selected.severity]?.text || selected.severity}</Pill>
                      <Pill level="neutral">Unresolved</Pill>
                    </div>
                    <div className="well detail-facts">
                      <div><div className="k">Affected subsystem</div><div>{subsystem(selected)}</div></div>
                      <div><div className="k">Alert key</div><div className="mono">{selected.key}</div></div>
                    </div>
                    <div className="detail-heading">Suggested next check</div>
                    <p className="detail-copy">{selected.remedy || 'No remedy supplied by the backend for this alert.'}</p>
                    {related.length > 0 && (
                      <>
                        <div className="detail-heading">Related preflight checks</div>
                        {related.map((i) => (
                          <div key={i.id} className="check-row raised" style={{ marginBottom: 6 }}>
                            <StatusIcon status={i.status} />
                            <div><div className="name">{i.name}</div><div className="msg">{i.message}</div></div>
                            <Pill level={CHECK_PILL[i.status]?.level}>{CHECK_PILL[i.status]?.text}</Pill>
                          </div>
                        ))}
                      </>
                    )}
                    <div className="detail-note"><IconUnknown /> Review is not resolution: acknowledging an alert does not repair the issue or clear preflight readiness.</div>
                  </>
                ) : <div className="empty">Select an alert.</div>}
              </GlassPanel>
            </div>
          </>
        )}

        <div className="action-dock">
          <div className="glass action-box">
            <button type="button" className="btn" onClick={() => onNavigate('navigate')}><IconNavigate /> Return to navigation</button>
            <button type="button" className="btn primary" onClick={() => onNavigate('preflight')}><IconPreflight /> Review preflight</button>
          </div>
        </div>
      </div>
    </>
  );
}
