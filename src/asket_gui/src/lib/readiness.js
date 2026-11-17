/**
 * The one line at the top of the screen: can the boat go, and if not, the
 * single most important reason why.
 *
 * Pure, so it can be tested without a browser. The order is the order an
 * operator would fix things in: nothing else is worth reading while no data
 * arrives, an RC link that is down outranks a pre-flight that is merely old,
 * and "disarmed" comes last because it is the normal state right up to the
 * moment of launch.
 *
 * Each reason is a short instruction, not a diagnosis: the panel below says
 * the rest.
 */

//: A pre-flight older than this no longer describes the boat in the water.
export const PREFLIGHT_MAX_AGE_MS = 60 * 60 * 1000;

export function readiness({ connected, receiving, report, pico, link, profile, nowMs }) {
  const notReady = (level, reason) => ({ level, ready: false, text: `NOT READY: ${reason}` });

  if (!connected) return notReady('alarm', 'no connection to the boat');
  if (!receiving) return notReady('alarm', 'no data from the boat');

  if (pico?.rc_link_ok === false) {
    return notReady('alarm', 'RC transmitter link lost — check the transmitter');
  }
  if (pico?.rc_lost_latched) {
    return notReady('alarm', 'the RC link was lost — flip Ch7 down then up');
  }
  if (pico?.mode === 'ESTOP') return notReady('alarm', 'E-stop — Ch8 is down, move it to the middle');
  if (pico?.version_mismatch) return notReady('alarm', 'Pico firmware does not match this software');

  if (link?.active_link === 'none') return notReady('alarm', 'no link to the shore station');

  if (!report) return notReady('warn', 'run the pre-flight');
  if (!report.go) {
    const first = (report.items || []).find((i) => i.status === 'FAIL');
    return notReady('alarm', `pre-flight NO-GO${first ? ` — ${first.name}` : ''}`);
  }
  if (nowMs && report.run_utc_ms && nowMs - report.run_utc_ms > PREFLIGHT_MAX_AGE_MS) {
    return notReady('warn', 'the pre-flight is over an hour old — run it again');
  }

  if (pico?.armed === false) {
    return notReady('warn', 'disarmed — flip Ch7 up to arm');
  }
  if (pico?.armed === undefined || pico?.armed === null) {
    return notReady('warn', 'the boat does not report whether it is armed');
  }
  if (pico?.rc_link_ok === undefined || pico?.rc_link_ok === null) {
    return notReady('warn', 'the RC link state is not reported');
  }
  if (profile === 'minimal') return notReady('warn', 'weak link — beacon data only');

  return { level: 'ok', ready: true, text: 'READY TO GO' };
}
