import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A beep for red alarms. Never for amber: an alarm an operator learns to
 * silence by reflex is worse than none.
 *
 * Browsers will not play sound until the page has been touched, so sound is
 * off until somebody presses "Enable sound" (remembered per browser, and
 * re-armed by the next click after a reload). "Silence" mutes the red alarms
 * showing now for SILENCE_MS; a NEW red alarm sounds at once regardless.
 *
 * Generated with WebAudio — no sound file to ship or to fail to load.
 */
const KEY = 'asket.alarm.sound';
export const SILENCE_MS = 5 * 60 * 1000;
export const BEEP_EVERY_MS = 4000;

/** The red alarms that should be making noise now. Pure, for the tests. */
export function audibleAlarms(alarms, silenced, nowMs) {
  return (alarms || []).filter(
    (a) => a.severity === 'alarm' && !(silenced[a.key] && silenced[a.key] > nowMs),
  );
}

/** Every red alarm showing now, silenced until nowMs + SILENCE_MS. */
export function silenceAll(alarms, nowMs) {
  const out = {};
  for (const a of alarms || []) if (a.severity === 'alarm') out[a.key] = nowMs + SILENCE_MS;
  return out;
}

function readEnabled() {
  try {
    return window.localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

function writeEnabled(on) {
  try {
    window.localStorage.setItem(KEY, on ? '1' : '0');
  } catch {
    // Survivable: the operator presses the button again after a reload.
  }
}

function beep(context) {
  const now = context.currentTime;
  for (const [offset, freq] of [[0, 880], [0.25, 660]]) {
    const osc = context.createOscillator();
    const gain = context.createGain();
    osc.type = 'square';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, now + offset);
    gain.gain.exponentialRampToValueAtTime(0.25, now + offset + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.2);
    osc.connect(gain).connect(context.destination);
    osc.start(now + offset);
    osc.stop(now + offset + 0.22);
  }
}

export function useAlarmSound(alarms) {
  const [enabled, setEnabled] = useState(readEnabled);
  const [silenced, setSilenced] = useState({});
  const [blocked, setBlocked] = useState(false);
  const context = useRef(null);

  const ensureContext = useCallback(() => {
    if (typeof window === 'undefined') return null;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;
    if (!context.current) context.current = new Ctor();
    if (context.current.state === 'suspended') context.current.resume().catch(() => {});
    return context.current;
  }, []);

  // After a reload the browser needs a gesture again: the next click anywhere
  // re-arms sound that the operator had already enabled.
  useEffect(() => {
    if (!enabled) return undefined;
    const arm = () => {
      ensureContext();
      setBlocked(false);
    };
    window.addEventListener('pointerdown', arm);
    return () => window.removeEventListener('pointerdown', arm);
  }, [enabled, ensureContext]);

  const audible = audibleAlarms(alarms, silenced, Date.now());
  const sounding = enabled && audible.length > 0;

  useEffect(() => {
    if (!sounding) return undefined;
    const play = () => {
      const ctx = ensureContext();
      if (!ctx) return;
      if (ctx.state !== 'running') {
        setBlocked(true);
        return;
      }
      beep(ctx);
    };
    play();
    const timer = setInterval(play, BEEP_EVERY_MS);
    return () => clearInterval(timer);
    // Re-run when the set of audible alarms changes, so a new one beeps now.
  }, [sounding, audible.map((a) => a.key).join('|'), ensureContext]);

  return {
    enabled,
    blocked,
    sounding,
    silencedCount: Object.values(silenced).filter((t) => t > Date.now()).length,
    enable: () => {
      writeEnabled(true);
      setEnabled(true);
      ensureContext();
    },
    disable: () => {
      writeEnabled(false);
      setEnabled(false);
    },
    silence: () => setSilenced(silenceAll(alarms, Date.now())),
  };
}
