import { Chip, Panel, Row, Rows } from '../components/Panel.jsx';
import { PanelAge, Value } from '../components/Value.jsx';
import { streamAgeMs, streamPayload } from '../lib/connection.js';
import { useDetails } from '../lib/details.js';
import { bearing, coordinate, num } from '../lib/format.js';
import { hullSummary } from '../lib/hull.js';
import { FIX_QUALITY_LABELS, MODE_LABELS } from '../lib/labels.js';
import { ModeButtons } from './ModeCommands.jsx';
import { GnssRows, HullDetail, VesselChips } from './VesselState.jsx';

/**
 * The boat: what mode it is in, whether it can move, and the buttons.
 *
 * The Vessel and Mode panels, merged, because on a beach they answer one
 * question — "what is the boat doing and what can I make it do" — and having
 * the reason it will not start in one panel and the button that will not work
 * in another was a scroll between cause and effect.
 *
 * Built from the same pieces as VesselState and ModeCommands (kept, and still
 * exported), so the wording and the confirmations are the same ones.
 *
 * Simple view: mode, armed, why it will not start, RC and Ch8, the buttons,
 * and one line of speed, heading and position quality. Details adds the
 * position, attitude, satellites, the relay and ESC rows.
 */
export function BoatPanel({ state, connection }) {
  const details = useDetails();
  const vessel = streamPayload(state, 'vessel');
  const pico = streamPayload(state, 'pico');
  const vesselAge = streamAgeMs(state, 'vessel');
  const picoAge = streamAgeMs(state, 'pico');
  const vesselRate = state.subscriptions.vessel?.rate_hz;
  const picoRate = state.subscriptions.pico?.rate_hz;
  const mode = pico?.mode ?? 'UNKNOWN';
  const hull = hullSummary(pico);

  const quality = vessel ? fixQuality(vessel) : null;

  return (
    <Panel title="Boat" aside={<PanelAge ageMs={picoAge} rateHz={picoRate} />}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <Value ageMs={picoAge} rateHz={picoRate}>
          <span className={`mode-badge mode-${mode}`}>{MODE_LABELS[mode] || mode}</span>
        </Value>
        <div className="spacer" />
        <Chip
          level={pico?.armed === undefined ? '' : pico.armed ? 'warn' : 'ok'}
          title={pico?.arming_block || undefined}
        >
          {pico?.armed === undefined ? 'Arming not sent' : pico.armed ? 'Armed' : 'Disarmed'}
        </Chip>
      </div>

      {/* Radio and Ch8, plus whatever is off-nominal about the Pico. */}
      <VesselChips pico={pico} mode={mode} />

      <p className="motion-line">
        <Value ageMs={vesselAge} rateHz={vesselRate} showAge={false}>
          {num(vessel?.sog_ms, 1, ' m/s')}
          {' · heading '}
          {vessel?.heading_valid ? bearing(vessel.heading_deg) : 'unknown'}
          {' · '}
          <span className={quality?.level || ''}>{quality?.text ?? 'no position sent'}</span>
        </Value>
      </p>

      {/* The "why it will not start" sentence is drawn by ModeButtons, above
          the buttons it explains. */}
      <ModeButtons state={state} connection={connection} />

      {hull.alert && (
        <p className="errline" style={{ marginBottom: 0, marginTop: 8 }}>
          {hull.alert}
        </p>
      )}

      {details && (
        <>
          <Rows>
            <Row label="Position">
              <Value ageMs={vesselAge} rateHz={vesselRate} showAge={false}>
                <span className="mono">{coordinate(vessel?.lat, vessel?.lon)}</span>
              </Value>
            </Row>
            <Row label="Roll / pitch">
              <Value ageMs={vesselAge} rateHz={vesselRate} showAge={false}>
                {num(vessel?.roll_deg, 1, '°')} / {num(vessel?.pitch_deg, 1, '°')}
              </Value>
            </Row>
            <GnssRows vessel={vessel} ageMs={vesselAge} rateHz={vesselRate} />
            {/* Only the real boat sends these (RosSource, full detail). */}
            {(vessel?.autopilot_expected || vessel?.autopilot_detected !== undefined) && (
              <Row label="Autopilot">
                <AutopilotValue
                  detected={vessel.autopilot_detected}
                  expected={vessel.autopilot_expected}
                />
              </Row>
            )}
          </Rows>
          <HullDetail hull={hull} />
        </>
      )}
    </Panel>
  );
}

/** "RTK fixed" / "RTK float" / "GPS only" / "no position", with a colour. */
export function fixQuality(vessel) {
  const q = vessel?.fix_quality;
  if (q === null || q === undefined) {
    // An older backend, or a fix with no status: say there is a position if
    // there is one, and nothing more.
    return vessel?.lat !== null && vessel?.lat !== undefined
      ? { text: 'position (quality not sent)', level: '' }
      : { text: FIX_QUALITY_LABELS.none, level: 'errline' };
  }
  return {
    text: FIX_QUALITY_LABELS[q] || q,
    level: q === 'none' ? 'errline' : q === 'gps' ? '' : 'okline',
  };
}

const AUTOPILOT_LABELS = { ardupilot: 'ArduPilot', px4: 'PX4' };

/** What the heartbeat says, against what the stack was launched for. */
function AutopilotValue({ detected, expected }) {
  const name = (id) => AUTOPILOT_LABELS[id] || id;
  if (!detected) {
    return <span className="dim-value">no heartbeat (launched for {name(expected) || '—'})</span>;
  }
  if (expected && detected !== expected) {
    return (
      <span className="errline">
        {name(detected)} — but launched for {name(expected)}
      </span>
    );
  }
  return <span>{name(detected)}</span>;
}
