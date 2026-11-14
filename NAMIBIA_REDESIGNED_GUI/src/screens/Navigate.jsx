import { useState } from 'react';
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
  IconAnchor, IconCamera, IconFlag, IconPin, IconPlay, IconPulse, IconSend, IconStop, IconWave,
} from '../components/Icons.jsx';
import { WorkspaceHeader } from '../components/Shell.jsx';
import { AreaMap } from '../slots/AreaMap.jsx';
import { CameraSlot, CAMERA_PILL, cameraLevel, segmentedLevel } from '../slots/CameraSlot.jsx';
import { bearingDeg, distanceM, estimateS, fmtKm, fmtMin, legs, routeLengthM } from '../lib/missionFile.js';
import { preflightVerdict, worstAlarm } from '../lib/vessel.js';
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
const notesFor = (v, connection, streams, state = v.state) => (
  <StreamNotes notes={streamNotes(connection, state, streams)} />
);
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

function StatusPanel({ v, connection }) {
  const { link, power, pico, vessel, alarms } = v;
  const connected = v.state.connected;
  const soc = power?.state_of_charge;
  const mode = pico?.mode;
  const fix = vessel?.fix_quality;
  const level = worstAlarm(alarms);
  const profile = v.state.profile?.profile;
  return (
    <GlassPanel title="Status" icon={<IconPulse />} flush={false} bodyClass="flush-body"
      notes={notesFor(v, connection, ['link', 'power', 'pico', 'vessel'])}
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

const fmtRange = (m) => (!Number.isFinite(m) ? '—' : m < 1000 ? `${m.toFixed(1)} m` : `${(m / 1000).toFixed(3)} km`);

/**
 * Distance and bearing from the autopilot's home (vessel.home_lat/lon, from
 * /mavros/home_position/home) and the furthest point of the track so far.
 * Range tests read this; it is the same on the boat network and in the cloud
 * view because the home comes from the boat, not from this browser.
 */
function HomeRange({ v, pos }) {
  const { vessel } = v;
  const home = Number.isFinite(vessel?.home_lat) && Number.isFinite(vessel?.home_lon)
    ? { lat: vessel.home_lat, lon: vessel.home_lon } : null;
  const here = Number.isFinite(vessel?.lat) && Number.isFinite(vessel?.lon)
    ? { lat: vessel.lat, lon: vessel.lon } : null;
  const track = streamPayload(v.state, 'track')?.points || [];
  if (!home) {
    return (
      <div className="home-range">
        <div className="label">From home</div>
        <span className="muted">{vessel ? 'No home from the autopilot yet (set at its first good fix)' : 'Not received'}</span>
      </div>
    );
  }
  const d = here ? distanceM(home, here) : NaN;
  const brg = here ? bearingDeg(home, here) : NaN;
  let max = Number.isFinite(d) ? d : 0;
  for (const [lon, lat] of track) max = Math.max(max, distanceM(home, { lat, lon }));
  return (
    <div className="home-range">
      <div>
        <div className="label">From home</div>
        <div className="big"><Value {...pos}>{fmtRange(d)}</Value></div>
      </div>
      <div>
        <div className="label">Bearing</div>
        <div className="big"><Value {...pos} showAge={false}>{Number.isFinite(brg) ? `${Math.round(brg)}°` : '—'}<small>{dir(brg)}</small></Value></div>
      </div>
      <div>
        <div className="label">Furthest</div>
        <div className="big"><Value {...pos} showAge={false}>{fmtRange(max)}</Value></div>
      </div>
      <div>
        <div className="label">Home</div>
        <div className="mono">{coord(home.lat, 'N', 'S')}<br />{coord(home.lon, 'E', 'W')}</div>
      </div>
    </div>
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

/** The shore link, reduced to the four values that matter on this screen. */
function LinkRow({ v }) {
  const { link } = v;
  const connected = v.state.connected;
  const age = live(v, 'link');
  const shown = (x) => (age && ageLevel(age) !== 'missing' ? <Value {...age}>{x}</Value> : x);
  const bearer = !connected ? 'Disconnected'
    : !link ? 'no data'
      : link.active_link === 'none' ? 'Lost'
        : LINK_LABELS[link.active_link] ?? 'not sent';
  const latency = !link ? '—' : Number.isFinite(link.rtt_ms) ? `${n(link.rtt_ms, 0)} ms` : 'not sent';
  const signal = !link ? '—' : link.rssi_dbm != null
    ? `${n(link.rssi_dbm, 0)} dBm${link.rssi_source === 'predicted' ? ' (from range)' : ''}`
    : 'not sent';
  const range = !link ? '—' : link.distance_m != null ? `${n(link.distance_m, 0)} m` : 'not sent';
  return (
    <div className="telemetry-link">
      <div>
        <div className="label">Link</div>
        <Pill level={!connected || link?.active_link === 'none' ? 'alarm' : !link ? 'neutral' : link.quality != null && link.quality < 0.4 ? 'warn' : 'ok'}>
          {shown(bearer)}
        </Pill>
      </div>
      <div><div className="label">Latency</div><div className="mono">{shown(latency)}</div></div>
      <div><div className="label">Signal</div><div className="mono">{shown(signal)}</div></div>
      <div><div className="label">Range</div><div className="mono">{shown(range)}</div></div>
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
    <GlassPanel title="Vessel telemetry" icon={<IconAnchor />} bodyClass="flush-body"
      notes={(
        <>
          {notesFor(v, connection, ['vessel', 'heading', 'link'])}
          <StreamNotes notes={streamNotes(connection, sonarState, ['sonar'])} />
        </>
      )}
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
        <LinkRow v={v} />
        <Compass heading={hdg} level={ageLevel(hdgAge)} />
      </div>
      <HomeRange v={v} pos={pos} />
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
  const powerAge = live(v, 'power');
  const here = Number.isFinite(vessel?.lat) ? { lat: vessel.lat, lon: vessel.lon } : null;

  return (
    <GlassPanel title="Mission" icon={<IconFlag />} className="mission-glass"
      notes={notesFor(v, connection, ['mission', 'power'])}
      aside={(
        <>
          <PanelAge {...live(v, 'mission')} />
          <Pill level={mission?.state === 'ERROR' ? 'alarm' : recording ? 'alarm' : 'neutral'}>
            {!mission ? 'No data' : mission.state ?? 'state not sent'}
          </Pill>
        </>
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
              failuresOnly
            />
            <ConfirmAction
              connection={connection} state={state} command="stop_mission"
              label="Abort" icon={<IconStop />} variant="danger"
              prompt="Stop the mission recording?"
              disabled={!recording}
              failuresOnly
            />
            <button
              type="button" className="btn info" disabled
              title="Route upload is not wired yet: gui_backend has no command to send waypoints to the vessel (see README)."
            >
              <IconSend /> Send to vessel
            </button>
          </div>
          <div className="mission-stats">
            <div>
              <div className="label">Mission time</div>
              <div className="stat">
                <Value {...live(v, 'mission')}>{!mission ? 'no data' : recording ? duration(mission.elapsed_s) : 'not recording'}</Value>
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

export function Navigate({ v, connection, active, onNavigate, sonarState = v.state }) {
  // Live feed or the segmented view (objects, masks, distances). The cloud cockpit has no
  // segmented stream (it mirrors snapshots only), so it stays on live.
  const [cameraView, setCameraView] = useState('live');
  const view = v.isCloud ? 'live' : cameraView;
  const cam = view === 'segmented' ? segmentedLevel(v.state) : cameraLevel(v.state);
  const verdict = preflightVerdict(v);
  const vessel = v.vessel;
  return (
    <>
      <WorkspaceHeader title="Navigation">
        <Pill level={verdict.level}>{verdict.text}</Pill>
      </WorkspaceHeader>
      <div className="workspace-body navigate-body">
        <div className="navigate">
          <div className="col left">
            <GlassPanel
              title="GPS basemap" icon={<IconPin />} flush
              notes={notesFor(v, connection, ['vessel', 'track'])}
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
              <AreaMap v={v} connection={connection} />
            </GlassPanel>
            <GlassPanel
              title="OAK camera" icon={<IconCamera />} flush
              notes={(
                <StreamNotes
                  notes={streamNotes(connection, v.state, ['camera']).filter((x) => x.kind !== 'refused')}
                />
              )}
              aside={<Pill level={CAMERA_PILL[cam.level]}>{cam.text}</Pill>}
              footer={(
                <div className="camera-controls">
                  <Pill level={v.mission?.state === 'RECORDING' ? 'alarm' : 'neutral'}>
                    {v.mission?.state === 'RECORDING' ? 'REC' : 'Not recording'}
                  </Pill>
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
          <div className="col right">
            <StatusPanel v={v} connection={connection} />
            <TelemetryPanel v={v} connection={connection} sonarState={sonarState} />
            <MissionPanel v={v} connection={connection} active={active} onNavigate={onNavigate} />
          </div>
        </div>
      </div>
    </>
  );
}
