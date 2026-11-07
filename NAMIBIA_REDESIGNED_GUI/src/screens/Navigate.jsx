import { MODE_LABELS } from '@asket/lib/labels.js';
import { duration, endurance } from '@asket/lib/format.js';

import { GlassPanel, Pill, ConfirmAction } from '../components/ui.jsx';
import {
  IconCamera, IconFlag, IconMap, IconPlay, IconPulse, IconSend, IconStop, IconWave,
} from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { GpsSlot } from '../slots/GpsSlot.jsx';
import { CameraSlot, CAMERA_PILL, cameraLevel } from '../slots/CameraSlot.jsx';
import { distanceM, estimateS, fmtKm, fmtMin, legs, routeLengthM } from '../lib/missionFile.js';
import { preflightVerdict, worstAlarm } from '../lib/vessel.js';

const n = (x, d = 1) => (Number.isFinite(x) ? x.toFixed(d) : '—');
const DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const dir = (deg) => (Number.isFinite(deg) ? DIRS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16] : '');
const coord = (v, pos, neg) => (Number.isFinite(v) ? `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}` : '—');

function StatusTile({ label, pill, level, sub }) {
  return (
    <div className="status-tile">
      <div className="label">{label}</div>
      <Pill level={level}>{pill}</Pill>
      <div className="sub">{sub}</div>
    </div>
  );
}

function StatusPanel({ v }) {
  const { link, power, pico, vessel, alarms } = v;
  const soc = power?.state_of_charge;
  const mode = pico?.mode;
  const fix = vessel?.fix_quality;
  const level = worstAlarm(alarms);
  return (
    <GlassPanel title="Status" icon={<IconPulse />} flush={false} bodyClass="flush-body"
      footer={(
        <div className={`alerts-strip ${level === 'ok' ? 'clear' : level}`}>
          <strong>{level === 'ok' ? '✓ NO ALERTS' : '⚠ ALERTS'}</strong>
          <span className="muted">
            {alarms.length ? alarms.map((a) => a.message).join(' · ') : 'Nothing needs attention.'}
          </span>
        </div>
      )}
    >
      <div className="status-grid" style={{ padding: 0 }}>
        <StatusTile
          label="Link"
          level={!link || link.active_link === 'none' ? 'alarm' : link.quality < 0.4 ? 'warn' : 'ok'}
          pill={link?.rssi_dbm != null ? `${n(link.rssi_dbm, 0)} dBm` : (link?.active_link ?? 'no data')}
          sub={link ? `${link.active_link} · ${n(link.rtt_ms, 0)} ms` : 'Not received'}
        />
        <StatusTile
          label="Profile"
          level={v.state.profile?.profile === 'full' ? 'ok' : v.state.profile ? 'warn' : 'neutral'}
          pill={v.state.profile?.profile ?? '—'}
          sub={link?.quality != null ? `quality ${Math.round(link.quality * 100)}%` : ''}
        />
        <StatusTile
          label="Battery"
          level={soc == null ? 'neutral' : soc < 0.25 ? 'alarm' : soc < 0.4 ? 'warn' : 'ok'}
          pill={soc != null ? `${Math.round(soc * 100)}%` : '—'}
          sub={power?.voltage != null ? `${n(power.voltage, 1)} V` : ''}
        />
        <StatusTile
          label="Mode"
          level={mode === 'ESTOP' ? 'alarm' : mode === 'AUTONOMOUS' ? 'info' : mode ? 'neutral' : 'neutral'}
          pill={MODE_LABELS[mode] ?? 'Unknown'}
          sub={pico?.rc_link_ok ? 'RC override available' : pico ? 'RC link not confirmed' : ''}
        />
        <StatusTile
          label="Arming"
          level={pico?.armed ? 'ok' : pico ? 'neutral' : 'neutral'}
          pill={pico?.armed == null ? '—' : pico.armed ? 'Armed' : 'Disarmed'}
          sub={pico?.estop_latched ? 'E-stop latched' : pico?.armed ? 'Thrusters enabled' : ''}
        />
        <StatusTile
          label="GPS"
          level={!fix || fix === 'none' ? 'alarm' : fix === 'gps' ? 'warn' : 'ok'}
          pill={vessel?.num_sats != null ? `${vessel.num_sats} sats` : (fix ?? '—')}
          sub={vessel?.hdop != null ? `HDOP ${n(vessel.hdop, 1)} · ${fix}` : fix ?? ''}
        />
      </div>
    </GlassPanel>
  );
}

function Compass({ heading }) {
  const ticks = Array.from({ length: 12 }, (_, i) => i * 30);
  return (
    <svg className="compass" viewBox="0 0 150 150">
      <circle cx="75" cy="75" r="62" fill="var(--glass-well)" stroke="var(--border)" />
      {ticks.map((t) => {
        const a = ((t - 90) * Math.PI) / 180;
        return (
          <g key={t}>
            <line x1={75 + 56 * Math.cos(a)} y1={75 + 56 * Math.sin(a)} x2={75 + 62 * Math.cos(a)} y2={75 + 62 * Math.sin(a)} stroke="var(--text-3)" />
            <text x={75 + 46 * Math.cos(a)} y={75 + 46 * Math.sin(a) + 3} fontSize="8" fill="var(--text-2)" textAnchor="middle">
              {{ 0: 'N', 90: 'E', 180: 'S', 270: 'W' }[t] ?? t}
            </text>
          </g>
        );
      })}
      {Number.isFinite(heading) && (
        <g transform={`rotate(${heading} 75 75)`}>
          <path d="M75 24 L81 75 L75 82 L69 75 Z" fill="#fff" />
          <path d="M75 126 L81 75 L69 75 Z" fill="var(--text-3)" />
        </g>
      )}
      <circle cx="75" cy="75" r="3" fill="var(--text)" />
    </svg>
  );
}

function TelemetryPanel({ v }) {
  const { vessel, heading, sonar } = v;
  const hdg = heading?.heading_deg ?? vessel?.heading_deg;
  const sonarUrl = v.state.hello?.source?.sonarview_url || import.meta.env.VITE_SONARVIEW_URL;
  return (
    <GlassPanel title="Vessel telemetry" icon={<IconFlag />} bodyClass="flush-body"
      footer={(
        <div className={`sonar-strip ${sonar?.connected ? '' : 'down'}`}>
          <IconWave />
          <strong className="upper">Sonar GUI</strong>
          <span className="muted">{sonar?.connected ? `${n(sonar.actual_ping_rate_hz, 1)} Hz ping` : 'Sonar not sending data'}</span>
          <span style={{ marginLeft: 'auto' }}>
            {sonarUrl
              ? <a href={sonarUrl} target="_blank" rel="noreferrer">Open Cerulean SonarView ↗</a>
              : <span className="faint">SonarView URL not configured</span>}
          </span>
        </div>
      )}
    >
      <div className="telemetry" style={{ padding: 0 }}>
        <div>
          <div className="label">Coordinates</div>
          <div className="mono" style={{ fontSize: 16 }}>{coord(vessel?.lat, 'N', 'S')}<br />{coord(vessel?.lon, 'E', 'W')}</div>
          <div className="label">Heading</div>
          <div className="big">{Number.isFinite(hdg) ? `${Math.round(hdg)}°` : '—'}<small>{dir(hdg)}</small></div>
          {vessel?.heading_valid === false && <Pill level="alarm">Heading invalid</Pill>}
        </div>
        <div>
          <div className="label">Speed</div>
          <div className="big">{n(vessel?.sog_ms, 1)}<small>m/s</small></div>
          <div style={{ display: 'flex', gap: 22 }}>
            <div><div className="label">Roll</div><div className="big" style={{ fontSize: 18 }}>{n(vessel?.roll_deg, 1)}°</div></div>
            <div><div className="label">Pitch</div><div className="big" style={{ fontSize: 18 }}>{n(vessel?.pitch_deg, 1)}°</div></div>
          </div>
        </div>
        <Compass heading={hdg} />
      </div>
    </GlassPanel>
  );
}

function MissionPanel({ v, connection, active, onNavigate }) {
  const { mission, power, vessel, state } = v;
  const recording = mission?.state === 'RECORDING';
  const name = active?.name || mission?.name || '';
  const wps = active?.waypoints || [];
  const legM = legs(wps);
  let cum = 0;
  const canFinish = power?.can_finish_survey;
  const here = Number.isFinite(vessel?.lat) ? { lat: vessel.lat, lon: vessel.lon } : null;

  return (
    <GlassPanel title="Mission" icon={<IconFlag />}
      aside={(
        <Pill level={mission?.state === 'ERROR' ? 'alarm' : recording ? 'alarm' : 'neutral'}>
          {mission?.state ? mission.state : 'No data'}
        </Pill>
      )}
      bodyClass="flush-body"
      footer={(
        <div className="mission-footer">
          <div className="btn-row">
            <ConfirmAction
              connection={connection} state={state} command="start_mission"
              args={{ name: (active?.name || 'mission').toLowerCase() }}
              label="Start mission" icon={<IconPlay />} variant="go"
              prompt="Start recording this mission?"
              disabled={!state.connected || recording}
            />
            <ConfirmAction
              connection={connection} state={state} command="stop_mission"
              label="Abort" icon={<IconStop />} variant="danger"
              prompt="Stop the mission recording?"
              disabled={!recording}
            />
            <button
              type="button" className="btn info" disabled
              title="Route upload is not wired yet: gui_backend has no command to send waypoints to the vessel (see README)."
            >
              <IconSend /> Send to vessel
            </button>
          </div>
          <div className="mission-stats">
            <div><div className="label">Mission time</div><div className="value">{recording ? duration(mission.elapsed_s) : '—'}</div></div>
            <div><div className="label">Battery</div><div className="value">{power?.state_of_charge != null ? `${Math.round(power.state_of_charge * 100)}%` : '—'}</div></div>
            <div>
              <div className="label">Est. left</div>
              <div className="value">{power?.endurance_s != null ? endurance(power.endurance_s) : '—'}</div>
              {canFinish != null && <Pill level={canFinish ? 'ok' : 'alarm'}>{canFinish ? 'Sufficient' : 'Insufficient'}</Pill>}
            </div>
          </div>
        </div>
      )}
    >
      <div className="mission-meta">
        <div>
          <div className="upper faint">Mission name</div>
          <div className="name">{name || <span className="muted">No mission selected</span>}</div>
        </div>
        {recording && mission?.name && <Pill level="neutral" dot={false}>Recording as {mission.name}</Pill>}
        {active && <span className="hint">{wps.length} waypoints · {fmtKm(routeLengthM(wps))} · {fmtMin(estimateS(active))}</span>}
        <button type="button" className="btn small" style={{ marginLeft: 'auto' }} onClick={() => onNavigate('repo')}>Choose mission</button>
      </div>
      <div style={{ padding: '6px 14px 10px', overflow: 'auto' }}>
        {wps.length ? (
          <table className="table">
            <thead>
              <tr><th>#</th><th>Coordinates</th><th>Leg</th><th>ETA from start</th><th>From vessel</th></tr>
            </thead>
            <tbody>
              {wps.map((w, i) => {
                cum += legM[i];
                return (
                  <tr key={i}>
                    <td><span className="wp-num">{i + 1}</span></td>
                    <td className="mono">{coord(w.lat, 'N', 'S')} · {coord(w.lon, 'E', 'W')}</td>
                    <td className="mono">{i === 0 ? 'Start' : `${Math.round(legM[i])} m`}</td>
                    <td className="mono">{i === 0 ? '—' : `${Math.round(cum / (active.cruise_speed_ms || 1.5) / 60)} min`}</td>
                    <td className="mono">{here ? `${Math.round(distanceM(here, w))} m` : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div className="empty">Pick a saved mission in Repo, or design one in Mission.</div>
        )}
        {wps.length > 0 && (
          <p className="hint" style={{ margin: '6px 0 0' }}>
            Waypoint progress is not reported by the vessel yet, so no waypoint is marked passed or current.
          </p>
        )}
      </div>
    </GlassPanel>
  );
}

export function Navigate({ v, connection, active, onNavigate }) {
  const cam = cameraLevel(v.state);
  const verdict = preflightVerdict(v);
  const vessel = v.vessel;
  return (
    <>
      <WorkspaceHeader title="Navigate" crumb="ASKET / Live operation">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body">
        <div className="navigate">
          <div className="col left">
            <GlassPanel
              title="GPS basemap" icon={<IconMap />} flush
              aside={(
                <Pill level={!vessel?.fix_quality || vessel.fix_quality === 'none' ? 'alarm' : 'ok'}>
                  {vessel?.fix_quality && vessel.fix_quality !== 'none' ? 'GPS lock' : 'No fix'}
                  {vessel?.num_sats != null ? ` · ${vessel.num_sats} sats` : ''}
                </Pill>
              )}
            >
              <GpsSlot v={v} connection={connection} />
            </GlassPanel>
            <GlassPanel
              title="OAK camera" icon={<IconCamera />} flush
              aside={<Pill level={CAMERA_PILL[cam.level]}>{cam.text}</Pill>}
              footer={(
                <div className="camera-controls">
                  <Pill level={v.mission?.state === 'RECORDING' ? 'alarm' : 'neutral'}>
                    {v.mission?.state === 'RECORDING' ? 'REC' : 'Not recording'}
                  </Pill>
                  <span className="feed-legend">
                    <span className={cam.level === 'live' ? 'on' : ''}>LIVE</span>|
                    <span className={cam.level === 'lagged' ? 'on' : ''}>LAGGED</span>|
                    <span className={cam.level === 'disconnected' ? 'on' : ''}>DISCONN</span>
                  </span>
                </div>
              )}
            >
              <CameraSlot v={v} />
            </GlassPanel>
          </div>
          <div className="col right">
            <StatusPanel v={v} />
            <TelemetryPanel v={v} />
            <MissionPanel v={v} connection={connection} active={active} onNavigate={onNavigate} />
          </div>
        </div>
      </div>
    </>
  );
}
