import { useRef, useState } from 'react';

import { CONFIRM_PROMPTS, LINK_LABELS, MODE_LABELS, PROFILE_LABELS } from '@asket/lib/labels.js';
import { streamAgeMs, streamPayload } from '@asket/lib/connection.js';
import { staleness, thresholdsForRate } from '@asket/lib/staleness.js';
import { PanelAge, Value } from '@asket/components/Value.jsx';
import { ModeButtons } from '@asket/panels/ModeCommands.jsx';
import { SonarPanel } from '@asket/panels/SonarPanel.jsx';
import { duration, endurance } from '@asket/lib/format.js';

import { GlassPanel, Pill, ConfirmAction, StreamNotes } from '../components/ui.jsx';
import { AsketEmbed } from '../components/Embed.jsx';
import {
  IconWarn,
  IconAnchor, IconCamera, IconChevronRight, IconFlag, IconPin, IconPulse, IconSend, IconStop, IconWave,
} from '../components/Icons.jsx';
import { AreaMap } from '../slots/AreaMap.jsx';
import { CameraSlot, CAMERA_PILL, cameraLevel, segmentedLevel } from '../slots/CameraSlot.jsx';
import { bearingDeg, distanceM, estimateS, fmtKm, fmtMin, legs, routeLengthM } from '../lib/missionFile.js';
import { worstAlarm } from '../lib/vessel.js';
import { useDeployedRoute } from '../lib/missionStore.js';
import {
  WAYPOINT_LABEL, deployedIsLive, isRunning, routeOf, routePill, startBlock, waypointStatus,
} from '../lib/route.js';
import { streamNotes } from '../lib/streamNotes.js';

const n = (x, d = 1) => (Number.isFinite(x) ? x.toFixed(d) : '—');
const DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const dir = (deg) => (Number.isFinite(deg) ? DIRS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16] : '');
const coord = (v, pos, neg) => (Number.isFinite(v) ? `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}` : '—');

/**
 * Age inputs for a stream (safety rule 7). `ageMs` is measured from the
 * value's source timestamp against the estimated server clock
 * (`streamAgeMs`); `rateHz` is the rate the server actually GRANTED, so a
 * slow stream on a narrow link is not painted stale while working as agreed.
 */
const live = (v, stream) => ({
  ageMs: streamAgeMs(v.state, stream),
  rateHz: v.state.subscriptions[stream]?.rate_hz,
});
const ageLevel = ({ ageMs, rateHz }) => staleness(ageMs, ...thresholdsForRate(rateHz));

/**
 * One tile of the status strip.
 *
 * `age` is the stream's age; a stale tile greys its pill out and the value
 * inside it is struck through, so an old green "OK" cannot read as a current
 * one. `pill` is a plain string: absent data is said in words ("no data",
 * "not sent"), never as a default that looks healthy.
 */
function StatusTile({ label, pill, level, sub, age, title }) {
  const stale = age ? ageLevel(age) : 'fresh';
  return (
    <div className={`status-tile ${stale}`} title={title}>
      <div className="label">{label}</div>
      <Pill level={stale === 'very-stale' ? 'neutral' : level}>
        {age && stale !== 'missing' ? <Value {...age} showAge={false}>{pill}</Value> : pill}
      </Pill>
      <div className="sub">
        {age && stale !== 'missing' ? <Value {...age}>{sub}</Value> : sub}
      </div>
    </div>
  );
}

/*
 * Short armed labels for the mode buttons. The reused ConfirmButton writes
 * "Confirm: <prompt>" in the button and puts the prompt in its title while
 * armed (and only then). The title is matched against the same CONFIRM_PROMPTS
 * the button uses, so the text shown becomes "Confirm manual" / "Confirm
 * autonomous"; the full question stays on hover. If the prompts ever change,
 * the selector stops matching and the button falls back to its own text.
 */
const ARMED_LABELS = [
  [CONFIRM_PROMPTS.set_mode_MANUAL, 'Confirm manual'],
  [CONFIRM_PROMPTS.set_mode_AUTONOMOUS, 'Confirm autonomous'],
];
const ARMED_CSS = ARMED_LABELS.map(([prompt, text]) => {
  const sel = `.mode-slim button[title^=${JSON.stringify(prompt)}]`;
  return `${sel}{font-size:0}${sel}::after{content:${JSON.stringify(text)};font-size:12px}`;
}).join('\n');

const rcLine = (ok) => (ok === true ? 'RC link OK' : ok === false ? 'RC link LOST' : 'RC link: not sent');

function StatusPanel({ v, connection, onNavigate }) {
  const { link, power, pico, vessel, alarms } = v;
  const connected = v.state.connected;
  const soc = power?.state_of_charge;
  const mode = pico?.mode;
  const fix = vessel?.fix_quality;
  const level = worstAlarm(alarms);
  const profile = v.state.profile?.profile;
  return (
    <GlassPanel title="Status" icon={<IconPulse />} flush={false} bodyClass="flush-body" collapsible="nav-status"
      footer={(
        <button
          type="button" className={`alerts-strip as-link ${level === 'ok' ? 'clear' : level}`}
          onClick={() => onNavigate('alerts')} title="Open Alerts"
        >
          <strong>{level === 'ok' ? '✓ NO ALERTS' : '⚠ ALERTS'}</strong>
          <span className="muted">
            {alarms.length ? alarms.map((a) => a.message).join(' · ') : 'Nothing needs attention.'}
          </span>
          <span className="spacer" />
          <IconChevronRight />
        </button>
      )}
    >
      <div className="status-grid" style={{ padding: 0 }}>
        <StatusTile
          label="Link"
          age={live(v, 'link')}
          level={
            !connected || link?.active_link === 'none'
              ? 'alarm'
              : !link || link.active_link == null
                ? 'neutral'
                : link.quality != null && link.quality < 0.4 ? 'warn' : 'ok'
          }
          pill={
            !connected ? 'Disconnected'
              : !link ? 'no data'
                : link.rssi_dbm != null ? `${n(link.rssi_dbm, 0)} dBm`
                  : LINK_LABELS[link.active_link] ?? 'not sent'
          }
          sub={link
            ? `${LINK_LABELS[link.active_link] ?? 'bearer not sent'} · ${link.rtt_ms != null ? `${n(link.rtt_ms, 0)} ms` : 'latency not sent'}`
            : 'Not received'}
        />
        <StatusTile
          label="Profile"
          level={!connected ? 'neutral' : profile === 'full' ? 'ok' : profile ? 'warn' : 'neutral'}
          pill={connected ? (PROFILE_LABELS[profile] ?? profile ?? 'unknown') : 'unknown'}
          sub={link?.quality != null ? `quality ${Math.round(link.quality * 100)}%` : 'quality not sent'}
        />
        <StatusTile
          label="Battery"
          age={live(v, 'power')}
          level={soc == null ? 'neutral' : soc < 0.25 ? 'alarm' : soc < 0.4 ? 'warn' : 'ok'}
          pill={!power ? 'no data' : soc != null ? `${Math.round(soc * 100)}%` : 'not sent'}
          sub={power ? (power.voltage != null ? `${n(power.voltage, 1)} V` : 'voltage not sent') : 'Not received'}
        />
        <StatusTile
          label="Mode"
          age={live(v, 'pico')}
          level={!pico || mode == null ? 'neutral' : mode === 'ESTOP' ? 'alarm' : mode === 'AUTONOMOUS' ? 'info' : 'neutral'}
          pill={!pico ? 'no data' : mode != null ? (MODE_LABELS[mode] ?? mode) : 'not sent'}
          sub={pico ? rcLine(pico.rc_link_ok) : 'Not received'}
        />
        <StatusTile
          label="Arming"
          age={live(v, 'pico')}
          level={pico?.arming_block ? 'warn' : pico?.armed ? 'ok' : 'neutral'}
          pill={!pico ? 'no data' : pico.armed == null ? 'not sent' : pico.armed ? 'Armed' : 'Disarmed'}
          /* Why propulsion cannot start (backend's arming_block): shown here,
             short, with the full sentence on hover, instead of as a paragraph
             above the mode buttons. */
          title={pico?.arming_block || undefined}
          sub={
            !pico ? 'Not received'
              : pico.arming_block ? 'Propulsion blocked'
              : pico.estop_latched === true ? 'Propulsion cut latched'
                : pico.estop_latched == null ? 'cut latch: not sent'
                  : pico.armed ? 'Thrusters enabled' : 'Thrusters off'
          }
        />
        <StatusTile
          label="GPS"
          age={live(v, 'vessel')}
          level={!vessel || fix == null ? 'neutral' : fix === 'none' ? 'alarm' : fix === 'gps' ? 'warn' : 'ok'}
          pill={!vessel ? 'no data' : vessel.num_sats != null ? `${vessel.num_sats} sats` : (fix ?? 'not sent')}
          sub={vessel
            ? `${vessel.hdop != null ? `HDOP ${n(vessel.hdop, 1)}` : 'HDOP not sent'} · ${fix ?? 'fix not sent'}`
            : 'Not received'}
        />
      </div>
      {/*
        Mode commands, minimal, inside Status: the existing ModeButtons from
        src/asket_gui, unmodified. Reused rather than rewritten because every
        rule in the developer guide's section 3 about modes lives in it:
        two-step confirmation, pending rendered unlike confirmed, the soft stop
        labelled "Cut propulsion", and all five NO-GO steps. The Mode tile
        above shows the mode the VESSEL reports (pico stream), never the one
        that was requested. Only the layout is slimmed (screens.css .mode-slim):
        Details off (the stop's effect stays in its tooltip and prompt), the
        arming-block paragraph moves to the Arming tile, and the confirm
        prompt and result stay on one line (full text on hover).
      */}
      <div className="mode-bar">
        <style>{ARMED_CSS}</style>
        <span className="mode-bar-label">Mode command</span>
        <AsketEmbed className="mode-embed mode-slim" details={false}>
          <ModeButtons state={v.state} connection={connection} />
        </AsketEmbed>
      </div>
    </GlassPanel>
  );
}

/** Gain as the sonar means it: int8, where -1 is automatic gain. */
export const gainLabel = (g) => (g == null ? 'not sent' : g < 0 ? 'auto' : String(g));

/**
 * The three words of the health view, exactly as SonarPanel.jsx's SonarHealth
 * decides them: alive = connected and fresher than 10 s, silent = anything
 * else, and no sonar stream at all = omniscan_bridge is not running, which is
 * said in words instead of left blank. Used only for the glass header pill;
 * the panel body is the reused SonarPanel itself.
 */
function sonarHealth(state) {
  const sonar = streamPayload(state, 'sonar');
  const ageMs = streamAgeMs(state, 'sonar');
  if (!sonar) return { level: 'warn', text: 'health not monitored' };
  const stale = ageMs !== null && ageMs > 10_000;
  return sonar.connected && !stale
    ? { level: 'ok', text: 'sonar alive' }
    : { level: 'alarm', text: 'sonar silent' };
}

/**
 * Sonar (developer guide section 5). Which panel is config, not code:
 * `hello.source.sonar_panel` is 'health' on the boat (SonarView runs the
 * sonar) and 'full' in the simulator and the mock. Both are the existing
 * SonarPanel from src/asket_gui, unmodified.
 *
 * No start/stop pinging control here, deliberately (guide section 5 point 2):
 * pinging is started by a person at the boat with ~/start_pinging.
 */
function SonarGlass({ state, connection }) {
  const health = state.hello?.source?.sonar_panel === 'health';
  const sonar = streamPayload(state, 'sonar');
  const url = state.hello?.source?.sonarview_url || '';
  const pill = health ? sonarHealth(state) : null;
  // An idle Omniscan is connected and sends status only; it pings only after
  // ~/start_pinging. "Alive" is true then, and so is "not pinging".
  const idle = health && pill.text === 'sonar alive' && !(sonar?.actual_ping_rate_hz > 0);
  return (
    <GlassPanel
      title="Sonar" icon={<IconWave />} className="sonar-glass"
      notes={<StreamNotes notes={streamNotes(connection, state, ['sonar'])} />}
      aside={pill && (
        <>
          <PanelAge ageMs={streamAgeMs(state, 'sonar')} rateHz={state.subscriptions.sonar?.rate_hz} />
          <Pill level={pill.level}>{pill.text}</Pill>
        </>
      )}
      footer={(
        <div className="sonar-foot">
          {!health && (url
            ? <a href={url} target="_blank" rel="noreferrer">Open SonarView ↗</a>
            : <span className="faint">No SonarView address (Setup page)</span>)}
          {sonar && <span>Gain <strong>{gainLabel(sonar.gain_setting)}</strong></span>}
          <span className="hint">
            SonarView and omniscan_bridge cannot both hold the sonar&rsquo;s port: one TCP client at a time.
          </span>
        </div>
      )}
    >
      {idle && (
        <p className="hint" style={{ margin: '0 0 8px' }}>
          Connected, not pinging. An idle sonar sends status only; pinging starts only
          when somebody at the boat calls <span className="mono">~/start_pinging</span>.
        </p>
      )}
      <AsketEmbed className={`sonar-embed ${health ? 'health' : 'full'}`}>
        <SonarPanel state={state} connection={connection} />
      </AsketEmbed>
    </GlassPanel>
  );
}

function Compass({ heading, level = 'fresh' }) {
  const ticks = Array.from({ length: 12 }, (_, i) => i * 30);
  return (
    <svg className={`compass ${level}`} viewBox="0 0 150 150">
      <circle cx="75" cy="75" r="62" fill="var(--glass-well)" stroke="var(--border)" />
      {ticks.map((t) => {
        const a = ((t - 90) * Math.PI) / 180;
        return (
          <g key={t}>
            <line x1={75 + 56 * Math.cos(a)} y1={75 + 56 * Math.sin(a)} x2={75 + 62 * Math.cos(a)} y2={75 + 62 * Math.sin(a)} stroke="var(--text-3)" />
            <text x={75 + 46 * Math.cos(a)} y={75 + 46 * Math.sin(a) + 3} fontSize="8" fill={t === 0 ? 'var(--alarm)' : 'var(--text-2)'} fontWeight={t === 0 ? 700 : 400} textAnchor="middle">
              {{ 0: 'N', 90: 'E', 180: 'S', 270: 'W' }[t] ?? t}
            </text>
          </g>
        );
      })}
      {Number.isFinite(heading) && level !== 'missing' && (
        <g transform={`rotate(${heading} 75 75)`}>
          <path d="M75 24 L81 75 L75 82 L69 75 Z" fill="#fff" />
          <path d="M75 126 L81 75 L69 75 Z" fill="var(--text-3)" />
        </g>
      )}
      <circle cx="75" cy="75" r="3" fill="var(--text)" />
    </svg>
  );
}

/**
 * Sonar as one strip in the telemetry box, styled like the alerts strip:
 * is it connected, and is it collecting. Same three health words as
 * sonarHealth() (alive / silent / not monitored), with "alive" split into
 * collecting vs. idle, because an idle Omniscan is connected but pings only
 * after ~/start_pinging.
 */
function sonarStrip(state) {
  const sonar = streamPayload(state, 'sonar');
  const ageMs = streamAgeMs(state, 'sonar');
  if (!sonar) {
    return { level: 'warn', head: 'SONAR NOT MONITORED', text: 'No sonar stream: omniscan_bridge is not running.' };
  }
  const stale = ageMs !== null && ageMs > 10_000;
  if (!sonar.connected || stale) {
    return {
      level: 'alarm',
      head: '⚠ SONAR SILENT',
      text: stale ? `No sonar data for ${Math.round(ageMs / 1000)} s.` : 'Sonar not connected.',
    };
  }
  const hz = sonar.actual_ping_rate_hz;
  return hz > 0
    ? { level: 'clear', head: '✓ SONAR CONNECTED', text: `Collecting data · ${n(hz, 1)} Hz` }
    : { level: 'warn', head: 'SONAR CONNECTED', text: 'Not collecting: pinging starts only with ~/start_pinging at the boat.' };
}

const fmtRange = (m) => (!Number.isFinite(m) ? '—' : m < 1000 ? `${m.toFixed(1)} m` : `${(m / 1000).toFixed(3)} km`);

/**
 * Distance and bearing from the autopilot's home (vessel.home_lat/lon, from
 * /mavros/home_position/home) and the furthest point of the track so far, as
 * on the Namibia_mission branch. The home comes from the boat, not from this
 * browser; without one the block says so instead of measuring from a guess.
 */
function HomeRange({ v, pos }) {
  const { vessel } = v;
  const home = Number.isFinite(vessel?.home_lat) && Number.isFinite(vessel?.home_lon)
    ? { lat: vessel.home_lat, lon: vessel.home_lon } : null;
  const here = Number.isFinite(vessel?.lat) && Number.isFinite(vessel?.lon)
    ? { lat: vessel.lat, lon: vessel.lon } : null;
  const track = streamPayload(v.state, 'track')?.points || [];
  const d = home && here ? distanceM(home, here) : NaN;
  const brg = home && here ? bearingDeg(home, here) : NaN;
  let max = Number.isFinite(d) ? d : NaN;
  if (home) {
    for (const [lon, lat] of track) {
      const x = distanceM(home, { lat, lon });
      if (Number.isFinite(x) && !(x <= max)) max = x;
    }
  }
  const shown = (x) => (home ? <Value {...pos} showAge={false}>{x}</Value> : x);
  return (
    <div className="telemetry-link home-range" title={home ? undefined : 'The autopilot sets home at its first good GPS fix.'}>
      <div><div className="label">From home</div><div className="mono">{home ? shown(fmtRange(d)) : vessel ? 'no home yet' : '—'}</div></div>
      <div><div className="label">Bearing</div><div className="mono">{shown(Number.isFinite(brg) ? `${Math.round(brg)}° ${dir(brg)}` : '—')}</div></div>
      <div><div className="label">Furthest</div><div className="mono">{shown(fmtRange(max))}</div></div>
      <div>
        <div className="label">Home</div>
        <div className="mono home-coords">{home ? <>{coord(home.lat, 'N', 'S')}<br />{coord(home.lon, 'E', 'W')}</> : '—'}</div>
      </div>
    </div>
  );
}

function TelemetryPanel({ v, connection, sonarState = v.state }) {
  const { vessel, heading } = v;
  const sonar = sonarStrip(sonarState);
  // The heading stream when it carries a heading, else the vessel's; the age
  // shown is the age of whichever one is displayed.
  const fromHeading = Number.isFinite(heading?.heading_deg);
  const hdg = fromHeading ? heading.heading_deg : vessel?.heading_deg;
  const pos = live(v, 'vessel');
  const hdgAge = fromHeading ? live(v, 'heading') : pos;
  return (
    <GlassPanel title="Vessel telemetry" icon={<IconAnchor />} bodyClass="flush-body" collapsible="nav-telemetry"
      aside={<PanelAge {...live(v, 'vessel')} />}
      footer={(
        <div className={`alerts-strip ${sonar.level}`}>
          <strong>{sonar.head}</strong>
          <span className="muted">{sonar.text}</span>
          <span className="spacer" />
          <PanelAge ageMs={streamAgeMs(sonarState, 'sonar')} rateHz={sonarState.subscriptions.sonar?.rate_hz} />
        </div>
      )}
    >
      <div className="telemetry" style={{ padding: 0 }}>
        <div>
          <div className="label">Coordinates</div>
          <div className="coords">
            <Value {...pos} showAge={false}>{coord(vessel?.lat, 'N', 'S')}</Value><br />
            <Value {...pos}>{coord(vessel?.lon, 'E', 'W')}</Value>
          </div>
          <div className="label">Heading</div>
          <div className="big heading">
            <Value {...hdgAge}>{Number.isFinite(hdg) ? `${Math.round(hdg)}°` : 'not sent'}<small>{dir(hdg)}</small></Value>
          </div>
          {(heading?.valid ?? vessel?.heading_valid) === false && <Pill level="alarm">Heading invalid</Pill>}
        </div>
        <div>
          <div className="label">Speed</div>
          <div className="big"><Value {...pos}>{n(vessel?.sog_ms, 1)}<small>m/s</small></Value></div>
          <div style={{ display: 'flex', gap: 22 }}>
            <div><div className="label">Roll</div><div className="big" style={{ fontSize: 18 }}><Value {...pos} showAge={false}>{n(vessel?.roll_deg, 1)}°</Value></div></div>
            <div><div className="label">Pitch</div><div className="big" style={{ fontSize: 18 }}><Value {...pos} showAge={false}>{n(vessel?.pitch_deg, 1)}°</Value></div></div>
          </div>
        </div>
        <HomeRange v={v} pos={pos} />
        <Compass heading={hdg} level={ageLevel(hdgAge)} />
      </div>
    </GlassPanel>
  );
}

/**
 * The route being followed, live.
 *
 * Shows the DEPLOYED route (what this server sent to mission_manager, fetched
 * from /api/routes/deployed) whenever mission_manager reports on it, and the
 * operator's selection from Repo otherwise. Waypoint status comes only from
 * /mission/status: passed = before `current_waypoint`, current = it.
 */
function BatteryPanel({ v }) {
  const packs = v.power?.packs || [];
  const age = live(v, 'power');
  const faulted = packs.some((p) => !p.comms || p.faults?.length || !p.dsg);
  return (
    <GlassPanel title="Battery packs" icon={<IconWarn />} collapsible="nav-battery"
      aside={<><PanelAge {...age} /><Pill level={!packs.length ? 'neutral' : faulted ? 'alarm' : 'ok'}>{!packs.length ? 'no data' : faulted ? 'Fault' : 'OK'}</Pill></>}>
      {!packs.length ? <span className="muted">No BMS reporting.</span> : (
        <div className="bms-packs">
          {packs.map((p) => {
            const low = p.cells_v?.length ? Math.min(...p.cells_v) : null;
            const high = p.cells_v?.length ? Math.max(...p.cells_v) : null;
            return (
              <div key={p.name} className={`bms-pack${p.stale ? ' stale' : ''}`}>
                <div className="bms-head">
                  <strong>{p.name}</strong>
                  {p.stale ? <Pill level="warn">{`silent ${n(p.age_s, 0)} s`}</Pill>
                    : !p.comms ? <Pill level="alarm">BMS not answering</Pill>
                      : p.faults?.length ? <Pill level="alarm">{p.faults.join(' ')}</Pill>
                        : !p.dsg ? <Pill level="alarm">Output off</Pill> : <Pill level="ok">Output on</Pill>}
                </div>
                <div className="bms-stats">
                  <span><b>{p.pack_v != null ? `${n(p.pack_v, 2)} V` : 'n/a'}</b>{p.soc != null ? ` · ${Math.round(p.soc * 100)}%` : ''}</span>
                  <span>{p.current_a != null ? `${n(p.current_a, 2)} A` : 'n/a'}</span>
                  <span>{p.temp_c != null ? `${n(p.temp_c, 1)} °C` : p.ntc === 1 ? 'NTC open' : p.ntc === 2 ? 'NTC short' : 'n/a'}</span>
                  <span>CHG {p.chg ? 'on' : 'off'} · DSG {p.dsg ? 'on' : 'off'}{p.retries ? ` · retries ${p.retries}` : ''}</span>
                </div>
                {p.cells_v?.length > 0 && (
                  <div className="bms-cells">
                    {p.cells_v.map((c, i) => <span key={i} title={`Cell ${i + 1}`}>{c.toFixed(3)}</span>)}
                    <span className="hint">Δ {Math.round((high - low) * 1000)} mV</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </GlassPanel>
  );
}

function MissionPanel({ v, connection, active, library, onNavigate, shown }) {
  const { mission, power, state } = v;
  const route = routeOf(v);
  const running = isRunning(route);
  const pill = routePill(route);
  const { isLive, mission: m } = shown;
  const wps = m?.waypoints || [];
  const legM = legs(wps);
  let cum = 0;
  const canFinish = power?.can_finish_survey;
  const powerAge = live(v, 'power');
  const here = Number.isFinite(v.vessel?.lat) ? { lat: v.vessel.lat, lon: v.vessel.lon } : null;
  const total = route?.total_waypoints;
  const reached = route?.state === 'SUCCEEDED' ? total : route?.current_waypoint;
  const pct = isLive && total ? Math.round((100 * (reached || 0)) / total) : 0;

  const canStart = active?.stored === 'server';
  const presets = (library?.missions || []).filter((r) => r.id.startsWith('nav-') || r.id === active?.id);
  const startWhy = !active ? 'Choose a mission first.'
    : !canStart ? 'This route is only in this browser. Upload it to the server in Repo.'
      : startBlock(v, connection) || undefined;

  return (
    <GlassPanel title="Mission" icon={<IconFlag />} className="mission-glass" collapsible="nav-mission"
      aside={(
        <>
          <PanelAge {...live(v, 'mission')} />
          <Pill level={pill.level}>{pill.text}</Pill>
        </>
      )}
      bodyClass="flush-body"
      footer={(
        <div className="mission-footer">
          <div className="btn-row">
            <ConfirmAction
              connection={connection} state={state} command="start_route"
              args={{ route_id: active?.id }}
              label="Start mission" icon={<IconSend />} variant="go"
              prompt={`Start ${active?.name || 'this mission'} (${active?.waypoints?.length || 0} waypoints)? The boat will drive itself.`}
              disabled={Boolean(startWhy)}
              title={startWhy || 'Requests Autonomous, then sends the route once the Pico confirms.'}
            />
            <ConfirmAction
              connection={connection} state={state} command="abort_route"
              label="Abort mission" icon={<IconStop />} variant="danger"
              prompt="Stop the mission and return to manual?"
              disabled={!running}
              title={running ? 'Cancels the route and drops to Manual: the RC sticks are live.' : 'No route is running.'}
            />
          </div>
          {startWhy && !running && <div className="launch-note">{startWhy}</div>}
          <div className="mission-stats">
            <div>
              <div className="label">Recording</div>
              <div className="stat">
                <Value {...live(v, 'mission')}>{!mission?.state ? 'no data' : mission.state === 'RECORDING' ? duration(mission.elapsed_s) : 'off'}</Value>
              </div>
            </div>
            <div>
              <div className="label">Battery</div>
              <div className="stat">
                <Value {...powerAge}>{!power ? 'no data' : power.state_of_charge != null ? `${Math.round(power.state_of_charge * 100)}%` : 'not sent'}</Value>
              </div>
            </div>
            <div>
              <div className="label">Est. left</div>
              <div className="stat">
                <Value {...powerAge}>{!power ? 'no data' : power.endurance_s != null ? endurance(power.endurance_s) : 'not sent'}</Value>
              </div>
              {canFinish != null && (
                <Pill level={ageLevel(powerAge) === 'very-stale' ? 'neutral' : canFinish ? 'ok' : 'alarm'}>
                  <Value {...powerAge} showAge={false}>{canFinish ? 'Sufficient' : 'Insufficient'}</Value>
                </Pill>
              )}
            </div>
          </div>
        </div>
      )}
    >
      {presets.length > 0 && !running && (
        <div className="mission-presets" role="group" aria-label="Choose a mission">
          {presets.map((r) => (
            <button key={r.id} type="button" aria-pressed={active?.id === r.id}
              className={`btn small${active?.id === r.id ? ' primary' : ''}`}
              onClick={() => library.setActive(r.id)}>{r.name}</button>
          ))}
        </div>
      )}
      <div className="mission-meta">
        <div>
          <div className="upper faint">{isLive ? 'Deployed route' : 'Selected route'}</div>
          <div className="name">{m?.name || <span className="muted">No route selected</span>}</div>
        </div>
        {m && <span className="hint">{wps.length} waypoints · {fmtKm(routeLengthM(wps))}{!isLive && ` · ${fmtMin(estimateS(m))}`}</span>}
        <button type="button" className="btn small" style={{ marginLeft: 'auto' }} onClick={() => onNavigate('repo')}>Choose route</button>
      </div>
      {isLive && (
        <div className="route-progress">
          <div className="route-progress-text">
            <strong>{route.state === 'SUCCEEDED' ? 'All waypoints passed' : `Waypoint ${Math.min((reached || 0) + 1, total)} of ${total}`}</strong>
            <span className="hint">{reached || 0} passed · {route.message}</span>
          </div>
          <div className={`route-bar ${pill.level}`} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      <div className="mission-wps">
        {wps.length ? (
          <table className="table">
            <thead>
              <tr><th>#</th><th>Status</th><th>Coordinates</th><th>Leg</th><th>From vessel</th></tr>
            </thead>
            <tbody>
              {wps.map((w, i) => {
                cum += legM[i];
                const st = isLive ? waypointStatus(route, i) : 'pending';
                return (
                  <tr key={i} className={`wp-${st}`}>
                    <td><span className="wp-num">{i + 1}</span></td>
                    <td><span className={`wp-status ${st}`}>{isLive ? WAYPOINT_LABEL[st] : '—'}</span></td>
                    <td className="mono wp-coord"><span>{coord(w.lat, 'N', 'S')}</span><span className="wp-sep"> · </span><span>{coord(w.lon, 'E', 'W')}</span></td>
                    <td className="mono">{i === 0 ? 'Start' : `${Math.round(legM[i])} m`}</td>
                    <td className="mono">{here ? `${Math.round(distanceM(here, w))} m` : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div className="empty">Pick a route in Repo, or import a .json there.</div>
        )}
      </div>
    </GlassPanel>
  );
}

/** Which route Navigate shows, and whether its status is live. */
function useShownRoute(v, connection, active) {
  const route = routeOf(v);
  const deployed = useDeployedRoute(connection, route?.deployed);
  // A finished or aborted route stays on screen only until another is selected.
  const live = deployedIsLive(route, deployed)
    && (isRunning(route) || !active || active.fingerprint === deployed.fingerprint);
  const mission = live
    ? { name: deployed.name, waypoints: deployed.waypoints, cruise_speed_ms: active?.cruise_speed_ms }
    : active;
  return {
    isLive: live,
    mission,
    selectionDiffers: live && active && active.fingerprint !== deployed.fingerprint,
    map: mission?.waypoints?.length
      ? {
        waypoints: mission.waypoints,
        statuses: mission.waypoints.map((_, i) => (live ? waypointStatus(route, i) : 'pending')),
      }
      : null,
  };
}

/** Recording (mission_recorder) start / stop, on the camera bar. */
function RecControl({ v, connection }) {
  const recording = v.mission?.state === 'RECORDING';
  return recording ? (
    <ConfirmAction
      connection={connection} state={v.state} command="stop_mission"
      label="Stop rec" icon={<IconStop />} variant="small danger"
      prompt="Stop recording?" failuresOnly
    />
  ) : (
    <ConfirmAction
      connection={connection} state={v.state} command="start_mission"
      args={{ name: 'mission' }}
      label="Rec" icon={<span className="rec-dot" />} variant="small rec"
      prompt="Start recording?" failuresOnly
      disabled={!v.state.connected}
      title="Starts mission_recorder (sonar, trajectory). Independent of the route."
    />
  );
}

const SPLIT_KEY = 'asket.redesign.navigate.split';
const SPLIT_DEFAULT = 49;
const SPLIT_MIN = 28;
const SPLIT_MAX = 72;
const clampSplit = (x) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, x));

function readSplit() {
  try {
    const x = Number(localStorage.getItem(SPLIT_KEY));
    return x ? clampSplit(x) : SPLIT_DEFAULT;
  } catch { return SPLIT_DEFAULT; }
}

/**
 * The divider between the map/camera column and the panels: drag it (or use
 * the arrow keys on it) to give the camera and map more width; double-click
 * puts it back. The width is the left column's share, remembered per browser.
 */
function useSplit(gridRef) {
  const [split, setSplit] = useState(readSplit);
  const save = (x) => { try { localStorage.setItem(SPLIT_KEY, String(Math.round(x * 10) / 10)); } catch { /* ignore */ } };
  const set = (x) => { const c = clampSplit(x); setSplit(c); save(c); };
  const onPointerDown = (e) => {
    const grid = gridRef.current;
    if (!grid) return;
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const rect = grid.getBoundingClientRect();
    document.body.classList.add('dragging-split');
    const move = (ev) => setSplit(clampSplit(((ev.clientX - rect.left) / rect.width) * 100));
    const up = () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      document.body.classList.remove('dragging-split');
      setSplit((x) => { save(x); return x; });
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); set(split - 2); }
    if (e.key === 'ArrowRight') { e.preventDefault(); set(split + 2); }
    if (e.key === 'Home') { e.preventDefault(); set(SPLIT_DEFAULT); }
  };
  const gutter = (
    <div
      className="nav-gutter" role="separator" aria-orientation="vertical" tabIndex={0}
      aria-valuemin={SPLIT_MIN} aria-valuemax={SPLIT_MAX} aria-valuenow={Math.round(split)}
      aria-label="Resize map and camera column" title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown} onKeyDown={onKeyDown} onDoubleClick={() => set(SPLIT_DEFAULT)}
    >
      <span />
    </div>
  );
  return { style: { '--nav-left': `${split}%` }, gutter };
}

export function Navigate({ v, connection, active, library, onNavigate, sonarState = v.state }) {
  // Live feed or the segmented view (objects, masks, distances). The cloud cockpit has no
  // segmented stream (it mirrors snapshots only), so it stays on live.
  const [cameraView, setCameraView] = useState('live');
  const view = v.isCloud ? 'live' : cameraView;
  const cam = view === 'segmented' ? segmentedLevel(v.state) : cameraLevel(v.state);
  const shown = useShownRoute(v, connection, active);
  const vessel = v.vessel;
  const gridRef = useRef(null);
  const split = useSplit(gridRef);
  return (
    <>
      <div className="workspace-body navigate-body">
        <div className="navigate" ref={gridRef} style={split.style}>
          <div className="col left">
            <GlassPanel
              title="GPS basemap" icon={<IconPin />} flush collapsible="nav-map"
              aside={(
                <>
                <PanelAge {...live(v, 'vessel')} />
                <Pill
                  level={
                    ageLevel(live(v, 'vessel')) === 'very-stale' ? 'neutral'
                      : !vessel?.fix_quality || vessel.fix_quality === 'none' ? 'alarm' : 'ok'
                  }
                >
                  {vessel?.fix_quality && vessel.fix_quality !== 'none' ? 'GPS lock' : 'No fix'}
                  {vessel?.num_sats != null ? ` · ${vessel.num_sats} sats` : ''}
                </Pill>
                </>
              )}
            >
              <AreaMap v={v} connection={connection} route={shown.map} />
            </GlassPanel>
            <GlassPanel
              title="OAK camera" icon={<IconCamera />} flush collapsible="nav-camera"
              aside={<Pill level={CAMERA_PILL[cam.level]}>{cam.text}</Pill>}
              footer={(
                <div className="camera-controls">
                  {v.mission?.state === 'RECORDING' && <Pill level="alarm">REC {duration(v.mission.elapsed_s)}</Pill>}
                  <RecControl v={v} connection={connection} />
                  {!v.isCloud && (
                    <span className="view-toggle" role="group" aria-label="Camera view">
                      <button type="button" className={view === 'live' ? 'on' : ''} aria-pressed={view === 'live'}
                        onClick={() => setCameraView('live')}>Live</button>
                      <button type="button" className={view === 'segmented' ? 'on' : ''} aria-pressed={view === 'segmented'}
                        onClick={() => setCameraView('segmented')}>Segmented + distance</button>
                    </span>
                  )}
                  <span className="feed-legend">
                    <span className={cam.level === 'live' ? 'on' : ''}>LIVE</span>|
                    <span className={cam.level === 'lagged' ? 'on' : ''}>LAGGED</span>|
                    <span className={cam.level === 'disconnected' ? 'on' : ''}>DISCONN</span>
                  </span>
                </div>
              )}
            >
              <CameraSlot v={v} view={view} />
            </GlassPanel>
            {/*
              Hidden, kept for later: the full Sonar panel (SonarGlass above).
              Sonar is now one strip at the bottom of Vessel telemetry, and
              link / latency / signal / range moved into that box too, so the
              Shore link panel is gone.

            <div className="row-bottom">
              <SonarGlass state={sonarState} connection={connection} />
            </div>
            */}
          </div>
          {split.gutter}
          <div className="col right">
            <StatusPanel v={v} connection={connection} onNavigate={onNavigate} />
            <TelemetryPanel v={v} connection={connection} sonarState={sonarState} />
            <BatteryPanel v={v} />
            <MissionPanel v={v} connection={connection} active={active} library={library} onNavigate={onNavigate} shown={shown} />
          </div>
        </div>
      </div>
    </>
  );
}
