import { cameraAgeMs } from '@asket/lib/connection.js';
import { isFitted } from '@asket/lib/fitted.js';

/**
 * OAK camera window = slot for the live feed (state.camera.url, the same frame
 * src/asket_gui's CameraPanel shows).
 *
 * The one rule carried over from CameraPanel: a frozen frame is the most
 * convincing lie a cockpit can tell, so a feed that has stopped is BLANKED,
 * not left on screen. LAGGED dims it; DISCONNECTED removes it.
 */
export function cameraLevel(state) {
  const camera = state.camera;
  if (!isFitted(state, 'camera')) return { level: 'not-fitted', text: 'Not fitted' };
  const sub = state.subscriptions?.camera;
  if (sub && !sub.granted) return { level: 'refused', text: 'Not carried', reason: sub.reason };
  if (!camera?.url) return { level: 'waiting', text: 'Waiting' };
  const age = cameraAgeMs(state);
  const interval = 1000 / (camera.rateHz || 5);
  if (age === null || age === undefined) return { level: 'waiting', text: 'Waiting' };
  if (age < Math.max(1500, 3 * interval)) return { level: 'live', text: 'Live', age };
  if (age < 5000) return { level: 'lagged', text: 'Lagged', age };
  return { level: 'disconnected', text: 'Disconnected', age };
}

export const CAMERA_PILL = {
  live: 'ok', lagged: 'warn', disconnected: 'alarm', waiting: 'neutral', refused: 'neutral', 'not-fitted': 'neutral',
};

export function CameraSlot({ v }) {
  const status = cameraLevel(v.state);
  const camera = v.state.camera;
  const showFrame = camera?.url && (status.level === 'live' || status.level === 'lagged');

  return (
    <div className="slot camera-slot">
      {showFrame ? (
        <>
          <img src={camera.url} alt="Forward camera" className={status.level === 'lagged' ? 'stale' : ''} />
          {status.level === 'lagged' && (
            <span className="age pill warn"><span className="dot" />{(status.age / 1000).toFixed(1)} s old</span>
          )}
        </>
      ) : (
        <div className="blank">
          <strong>
            {{
              'not-fitted': 'No camera fitted on this vessel',
              refused: 'Camera not carried on this link',
              waiting: 'Waiting for the first frame',
              disconnected: 'Camera feed stopped',
            }[status.level]}
          </strong>
          <span className="hint">
            {status.level === 'disconnected'
              ? `Last frame ${(status.age / 1000).toFixed(0)} s ago. The picture is hidden so an old frame is not mistaken for the view now.`
              : status.level === 'refused'
                ? status.reason || 'The current link profile does not carry video.'
                : 'The live OAK feed appears here.'}
          </span>
        </div>
      )}
    </div>
  );
}
