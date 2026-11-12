import { Banner, CHECK_PILL, ConfirmAction, Pill, StatusIcon } from '../components/ui.jsx';
import { Emblem, IconMission, IconNavigate, IconRefresh } from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { preflightVerdict } from '../lib/vessel.js';

/**
 * Welcome / Preflight — the M0 harbour-acceptance gate from
 * "Mission controls and documents/KELP_MISSIONS.md", fed by the backend's
 * passive pre-flight report (`diagnostics` stream, `run_system_test` command).
 * Same three states as the Figma: first connection, NO-GO, ready to go.
 */

// Control, safety & position on the left; attitude, perception & time on the
// right — the grouping in the Figma.
const LEFT = /^(pico|rc|estop|e_stop|safety|gnss|heading|battery|power|relay|esc)/;
const STORAGE = /^disk\.space/;
// The two summaries under the checklist (Figma 02 / 03).
const SAFETY = /^(pico|rc|estop|e_stop|safety|relay|esc)/;
const SENSORS = /^(lidar|sonar|camera|oak)/;

/**
 * "02 / Safety chain verified" or "02 / Verify the safety chain": the title
 * says which only when every check in the group passed; the body lists what
 * is still open, in the report's own remedy words.
 */
function Summary({ n, items, done, todo, closing, closingWarn }) {
  if (!items.length) return null;
  const open = items.filter((i) => i.status !== 'PASS');
  const ok = open.length === 0;
  return (
    <div className="pf-summary">
      <div className={`pf-summary-title ${ok ? 'ok' : ''}`}>{n} / {ok ? done : todo}</div>
      <p>
        {ok
          ? `${items.map((i) => i.name).join(', ')} passed.`
          : open.map((i) => `${i.name}: ${i.remedy || i.message}`).join(' ')}
      </p>
      <p className={ok ? '' : closingWarn ? 'warn-strong' : ''}>{ok ? closing.ok : closing.open}</p>
    </div>
  );
}

function CheckRow({ item }) {
  const pill = CHECK_PILL[item.status] || CHECK_PILL.SKIPPED;
  return (
    <div className="check-row">
      <StatusIcon status={item.status} />
      <div>
        <div className="name">{item.name}</div>
        <div className="msg">{item.message}</div>
        {item.status !== 'PASS' && item.remedy && <div className="remedy">{item.remedy}</div>}
      </div>
      <Pill level={pill.level}>{pill.text}</Pill>
    </div>
  );
}

function ago(ms) {
  if (!ms) return '';
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

export function Preflight({ v, connection, onNavigate }) {
  const { report, state } = v;
  const verdict = preflightVerdict(v);
  const items = report?.items || [];
  const storage = items.find((i) => STORAGE.test(i.id));
  const rest = items.filter((i) => i !== storage);
  const left = rest.filter((i) => LEFT.test(i.id));
  const right = rest.filter((i) => !LEFT.test(i.id));
  const count = (s) => items.filter((i) => i.status === s).length;

  const runButton = (
    <ConfirmAction
      connection={connection} state={state} command="run_system_test"
      label={report ? (report.go ? 'Re-run checks' : 'Re-run passive checks') : 'Run passive checks'}
      prompt="Run the passive pre-flight checks now?"
      icon={<IconRefresh />} disabled={!state.connected}
    />
  );
  let banner;
  if (!state.connected) {
    banner = (
      <Banner level="info" title="Connect to ASKET to begin"
        detail={state.connecting ? 'Connecting to the vessel…' : 'No vessel connection. Checks run once component data arrives.'} actions={runButton} />
    );
  } else if (!report) {
    banner = (
      <Banner level="warn" title="Preflight not run"
        detail="Run the passive checks before choosing navigation or mission planning." actions={runButton} />
    );
  } else if (!report.go) {
    banner = <Banner level="warn" title="NO-GO — launch is not permitted" detail={report.summary} actions={runButton} />;
  } else {
    banner = (
      <Banner level="ok" title="Ready to go"
        detail={`${report.summary}. Arming and launch remain separate operator actions.`} actions={runButton} />
    );
  }

  return (
    <>
      <WorkspaceHeader title="Preflight">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="glass preflight-card">
          <div className="preflight-title">
            <Emblem />
            <div>
              <h2>Welcome to ASKET</h2>
              <div className="muted">Review the vessel’s passive checks before choosing navigation or mission planning.</div>
            </div>
          </div>

          <div className="pf-banner">{banner}</div>

          <div style={{ display: 'flex', alignItems: 'center' }}>
            <span className="pf-step">01 / Review all checkpoints</span>
            {report && <span className="hint" style={{ marginLeft: 10 }}>Run {ago(report.run_utc_ms)}</span>}
            {report && (
              <span className="counts">
                <Pill level="ok">{count('PASS')} OK</Pill>
                {report.go && <Pill level="ok">Preflight complete</Pill>}
                {count('WARN') > 0 && <Pill level="warn">{count('WARN')} warning{count('WARN') === 1 ? '' : 's'}</Pill>}
                {count('FAIL') > 0 && <Pill level="alarm">{count('FAIL')} fail</Pill>}
                {count('SKIPPED') > 0 && <Pill level="neutral">{count('SKIPPED')} unknown</Pill>}
              </span>
            )}
          </div>

          {report ? (
            <div className="checks">
              <div className="check-group raised">
                <div className="group-title">Control, safety &amp; position</div>
                {left.map((i) => <CheckRow key={i.id} item={i} />)}
              </div>
              <div className="check-group raised">
                <div className="group-title">Attitude, perception &amp; time</div>
                {right.map((i) => <CheckRow key={i.id} item={i} />)}
              </div>
            </div>
          ) : (
            <div className="raised empty">
              {state.connected ? 'No pre-flight report yet. Run the passive checks.' : 'Awaiting vessel connection · no checks have run.'}
            </div>
          )}

          {storage && (
            <div className={`storage-row banner ${storage.status === 'PASS' ? 'ok' : 'warn'}`}>
              <StatusIcon status={storage.status} />
              <span className="mono">{storage.message}</span>
              {storage.status !== 'PASS' && <span className="muted">{storage.remedy}</span>}
            </div>
          )}

          {report && (
            <div className="pf-summaries">
              <Summary
                n="02" items={items.filter((i) => SAFETY.test(i.id))}
                done="Safety chain verified" todo="Verify the safety chain" closingWarn
                closing={{
                  ok: 'Safety feedback confirmed. Vessel remains disarmed until a separate operator action.',
                  open: 'Until verified, the hardware killswitch and RC channel 8 are the only confirmed propulsion cuts.',
                }}
              />
              <Summary
                n="03" items={items.filter((i) => SENSORS.test(i.id))}
                done="Required sensors verified" todo="Confirm required sensors"
                closing={{
                  ok: 'Passive checks only. Motor tests never run automatically and are not available from this panel.',
                  open: 'Passive checks only. Motor tests never run automatically and are not available from this panel.',
                }}
              />
            </div>
          )}
          {!report && (
            <p className="hint" style={{ margin: 0 }}>
              Passive checks only. Motor tests never run automatically and are not available from this panel.
              Until the e-stop feedback is verified, the hardware killswitch and RC channel 8 are the only confirmed propulsion cuts.
            </p>
          )}
          <div className="next-steps">
            <span className="hint" style={{ flex: 1 }}>Neither option arms nor launches ASKET.</span>
            <button type="button" className="btn" onClick={() => onNavigate('navigate')}><IconNavigate /> Continue to navigation</button>
            <button type="button" className="btn primary" onClick={() => onNavigate('mission')}><IconMission /> Design a mission</button>
          </div>
        </div>
      </div>
    </>
  );
}
