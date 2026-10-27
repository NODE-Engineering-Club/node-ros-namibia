import { useState } from 'react';

import { Panel } from '../components/Panel.jsx';
import { ConfirmButton } from '../components/ConfirmButton.jsx';
import { streamPayload } from '../lib/connection.js';
import { COMMAND_LABELS, COMMAND_STATUS_LABELS, CONFIRM_PROMPTS } from '../lib/labels.js';
import { useDetails } from '../lib/details.js';

/**
 * The three mode commands.
 *
 * Safety rules 3, 4 and 5 all land here:
 *
 * - the soft ESTOP is labelled **Cut propulsion**, never "Emergency stop";
 * - each command is two-step: click to arm, click to send;
 * - the *displayed* mode comes from the vessel and lives in the Vessel panel.
 *   Nothing in here changes it. A command in flight shows as an outlined,
 *   pulsing button and a "waiting for the vessel" line, and it either becomes
 *   a confirmation from the Pico or an explicit failure.
 */
export function ModeCommands({ state, connection }) {
  return (
    <Panel title="Mode">
      <ModeButtons state={state} connection={connection} />
    </Panel>
  );
}

/** The buttons and their outcome line, shared with the Boat panel. */
export function ModeButtons({ state, connection }) {
  const details = useDetails();
  const pico = streamPayload(state, 'pico');
  const mode = pico?.mode;

  const recent = Object.values(state.commands)
    .filter((c) => c.name === 'set_mode' || c.name === 'cut_propulsion')
    .sort((a, b) => (a.issued_utc_ms || 0) - (b.issued_utc_ms || 0));
  const latest = recent[recent.length - 1];
  const inFlight = recent.filter((c) => c.status === 'pending');

  const pendingFor = (name, argMode) =>
    inFlight.some((c) => c.name === name && (!argMode || c.args?.mode === argMode));

  const offline = !state.connected;

  // Why the propellers cannot turn, as the backend computed it. Arming is RC
  // channel 7 and nothing else — there is no software path to it, deliberately
  // — so a mode command here can be accepted, engaged and confirmed by the
  // vessel while nothing moves.
  //
  // The button is **not** disabled when this is set. Selecting AUTONOMOUS and
  // then arming on the transmitter is the normal launch sequence, with
  // somebody standing next to the boat; disabling would break that sequence,
  // whereas saying so makes the order legible.
  const armingBlock = pico?.arming_block || '';

  const withArmingBlock = (prompt) => (armingBlock ? `${prompt} ${armingBlock}.` : prompt);

  // Refused by the backend's NO-GO gate. The way past it is two deliberate
  // acts — tick the box, then the two-step button — and the backend logs it.
  // An aid, not a safety: the transmitter remains the authority.
  const nogoRefused = latest?.name === 'set_mode' && latest?.status === 'failed' && latest?.nogo;
  const [nogoAcknowledged, setNogoAcknowledged] = useState(false);

  // The vessel declares what a propulsion cut can do here; the GUI does not
  // assume it. Falls back to the honest minimum before the hello arrives.
  const declared = state.hello?.source?.estop;
  const estop = {
    available: declared?.available ?? false,
    label: declared?.label || COMMAND_LABELS.cut_propulsion,
    effect: declared?.effect || '',
    // A MANUAL request on the real stack; a latched ESTOP only in the simulator.
    isModeRequest: declared?.mode === 'mode_request_manual',
    prompt: declared?.effect
      ? `${declared.label || COMMAND_LABELS.cut_propulsion}? ${declared.effect}`
      : CONFIRM_PROMPTS.cut_propulsion,
  };

  return (
    <>
      {/* Standing, above the buttons, because the state exists before
          anybody presses one. Without it an operator who presses
          Autonomous, sees the vessel confirm AUTONOMOUS, and watches
          nothing turn has no way to tell whether the boat ignored them
          or the arm switch did. */}
      {armingBlock && (
        <p className="warnline" style={{ marginTop: 0, marginBottom: 8 }}>
          {armingBlock}
        </p>
      )}
      <div className="button-row">
        <ConfirmButton
          label={COMMAND_LABELS.set_mode_MANUAL}
          prompt={CONFIRM_PROMPTS.set_mode_MANUAL}
          pending={pendingFor('set_mode', 'MANUAL')}
          disabled={offline || mode === 'MANUAL'}
          onConfirm={() => connection.command('set_mode', { mode: 'MANUAL' })}
        />
        <ConfirmButton
          label={COMMAND_LABELS.set_mode_AUTONOMOUS}
          prompt={withArmingBlock(CONFIRM_PROMPTS.set_mode_AUTONOMOUS)}
          pending={pendingFor('set_mode', 'AUTONOMOUS')}
          disabled={offline || mode === 'AUTONOMOUS'}
          onConfirm={() => connection.command('set_mode', { mode: 'AUTONOMOUS' })}
        />
      </div>

      <div className="button-row" style={{ marginTop: 8 }}>
        <ConfirmButton
          danger
          label={estop.label}
          prompt={estop.prompt}
          pending={pendingFor('cut_propulsion')}
          disabled={offline || !estop.available}
          onConfirm={() => connection.command('cut_propulsion', {})}
        />
      </div>

      {/* What this button does depends on the vessel, and the vessel says so.
          On the Njord stack there is no software ESTOP at all: pico_bridge
          accepts AUTO and MANUAL and nothing else. A button labelled "Cut
          propulsion" that quietly drops the mode instead would be the single
          most dangerous thing on this screen, so it is labelled with what it
          actually does and the effect is spelled out underneath. */}
      {estop.effect && (
        <p className="warnline" style={{ marginBottom: 0, marginTop: 6 }}>
          {estop.effect}
        </p>
      )}

      {nogoRefused && mode !== 'AUTONOMOUS' && (
        <div className="nogo-force">
          <label>
            <input
              type="checkbox"
              checked={nogoAcknowledged}
              onChange={(e) => setNogoAcknowledged(e.target.checked)}
            />{' '}
            I have read the pre-flight and accept the NO-GO
          </label>
          <ConfirmButton
            danger
            label="I know it is NO-GO, force"
            prompt="Send the boat autonomous over a NO-GO pre-flight? This is logged."
            disabled={offline || !nogoAcknowledged}
            pending={pendingFor('set_mode', 'AUTONOMOUS')}
            onConfirm={() => {
              setNogoAcknowledged(false);
              connection.command('set_mode', { mode: 'AUTONOMOUS', force_nogo: true });
            }}
          />
        </div>
      )}

      {latest && (
        <p
          className={
            latest.status === 'failed' ? 'errline' : latest.status === 'pending' ? 'hint' : 'hint'
          }
          style={{ marginBottom: 0 }}
        >
          {describe(latest, armingBlock, estop.label)}
        </p>
      )}

      {details && (
        <p className="hint" style={{ marginBottom: 0, marginTop: 8 }}>
          {estop.isModeRequest
            ? 'This is a mode request, not a latch.'
            : 'This is a software latch.'}{' '}
          The hardware killswitch and RC channel&nbsp;8 cut propulsion independently
          and always work.
        </p>
      )}
    </>
  );
}

function describe(command, armingBlock = '', estopLabel = COMMAND_LABELS.cut_propulsion) {
  const what =
    command.name === 'cut_propulsion'
      ? estopLabel
      : `Mode → ${command.args?.mode ?? ''}`;
  const status = COMMAND_STATUS_LABELS[command.status] || command.status;
  if (command.status === 'failed') return `${what}: ${status}. ${command.detail || ''}`;
  // "Confirmed by the vessel" and "nothing is turning" are both true at once
  // whenever the arm switch is down, and an operator reading only the first
  // has been told the truth and misled anyway. The two are reported together
  // or the confirmation is a half-answer.
  if (command.status === 'confirmed' && armingBlock) {
    return `${what}: ${status} — but ${armingBlock}`;
  }
  return `${what}: ${status}`;
}
