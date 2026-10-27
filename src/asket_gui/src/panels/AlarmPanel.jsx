import { Panel } from '../components/Panel.jsx';
import { useAlarmSound } from '../lib/alarmSound.js';

/**
 * Active alarms.
 *
 * Deliberately few. Only conditions that cannot self-correct and that need a
 * person to do something appear here; obstacle detection, for instance, does
 * not, because avoidance is the navigation stack's job and an alarm an operator
 * learns to dismiss is worse than no alarm.
 *
 * Every entry carries a remedy, because a red word is not an instruction.
 *
 * Red alarms beep (lib/alarmSound.js) once sound is enabled; amber ones never
 * do. "Silence" mutes the red alarms showing now for five minutes.
 */
export function AlarmPanel({ state }) {
  const alarms = state.alarms || [];
  const sound = useAlarmSound(alarms);
  const anyRed = alarms.some((a) => a.severity === 'alarm');

  const controls = (
    <div className="button-row alarm-sound">
      {sound.enabled ? (
        <>
          {anyRed && (
            <button type="button" onClick={sound.silence} disabled={!sound.sounding}>
              Silence 5 min
            </button>
          )}
          <button type="button" className="link" onClick={sound.disable}>
            Sound off
          </button>
        </>
      ) : (
        <button type="button" onClick={sound.enable} title="Red alarms beep. Amber ones never do.">
          Enable sound
        </button>
      )}
    </div>
  );

  if (alarms.length === 0) {
    return (
      <Panel title="Alarms" aside={controls}>
        <p className="hint" style={{ margin: 0 }}>Nothing needs attention.</p>
      </Panel>
    );
  }

  return (
    <Panel title={`Alarms (${alarms.length})`} aside={controls}>
      {sound.blocked && (
        <p className="warnline" style={{ marginTop: 0 }}>
          The browser is blocking sound: click anywhere on the page to allow it.
        </p>
      )}
      {sound.silencedCount > 0 && !sound.sounding && anyRed && (
        <p className="hint" style={{ marginTop: 0 }}>
          Silenced for 5 minutes. A new red alarm will sound.
        </p>
      )}
      <div className="alarm-list">
        {alarms.map((alarm) => (
          <div key={alarm.key} className={`alarm-item ${alarm.severity}`}>
            <div className="message">{alarm.message}</div>
            {alarm.remedy && <div className="remedy">{alarm.remedy}</div>}
          </div>
        ))}
      </div>
    </Panel>
  );
}
