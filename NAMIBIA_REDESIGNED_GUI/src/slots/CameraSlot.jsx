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

/**
 * The segmented view (stream "camera_seg"): camera frames with each object's
 * mask, label and distance drawn on them by the boat. Normally the OAK's
 * on-camera detector (~10 fps); with SAM 2 a new one comes every few seconds,
 * so the thresholds are in seconds, not frame intervals. The rule is the same
 * as the live view: its age is the age of the PICTURE (the camera stamp), and
 * a result that has stopped is blanked.
 */
export const SEG_LIVE_MS = 15000;
export const SEG_LAGGED_MS = 30000;

export function segmentedLevel(state) {
  if (!isFitted(state, 'camera')) return { level: 'not-fitted', text: 'Not fitted' };
  const sub = state.subscriptions?.camera_seg;
  if (sub && !sub.granted) return { level: 'refused', text: 'Not carried', reason: sub.reason };
  const seg = state.cameraSeg;
  if (!seg?.url || !seg.sourceUtcMs) return { level: 'waiting', text: 'Waiting' };
  const age = Date.now() + (state.skewMs || 0) - seg.sourceUtcMs;
  if (age < SEG_LIVE_MS) return { level: 'live', text: 'Segmented', age };
  if (age < SEG_LAGGED_MS) return { level: 'lagged', text: 'Lagged', age };
  return { level: 'disconnected', text: 'Stopped', age };
}

export const CAMERA_PILL = {
  live: 'ok', lagged: 'warn', disconnected: 'alarm', waiting: 'neutral', refused: 'neutral', 'not-fitted': 'neutral',
};

export function CameraSlot({ v, view = 'live' }) {
  if (view === 'segmented') return <SegmentedSlot v={v} />;
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
          {v.isCloud && status.level === 'live' && (
            // Cloud view: one snapshot every 30 s, so always say how old it is.
            <span className="age pill neutral"><span className="dot" />Snapshot, {(status.age / 1000).toFixed(0)} s old</span>
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

function SegmentedSlot({ v }) {
  const status = segmentedLevel(v.state);
  const seg = v.state.cameraSeg;
  const showFrame = seg?.url && (status.level === 'live' || status.level === 'lagged');
  return (
    <div className="slot camera-slot segmented">
      {showFrame ? (
        <>
          <img src={seg.url} alt="Camera frame with each detected object's mask, label and distance" className={status.level === 'lagged' ? 'stale' : ''} />
          <span className={`age pill ${status.level === 'lagged' ? 'warn' : 'neutral'}`}>
            <span className="dot" />{status.age < 1500 ? 'Live, objects and distances' : `Segmented frame, ${(status.age / 1000).toFixed(1)} s old`}
          </span>
        </>
      ) : (
        <div className="blank">
          <strong>
            {{
              'not-fitted': 'No camera fitted on this vessel',
              refused: 'Segmentation not carried on this link',
              waiting: 'Waiting for the first segmented frame',
              disconnected: 'Segmentation stopped',
            }[status.level]}
          </strong>
          <span className="hint">
            {status.level === 'disconnected'
              ? `Last result ${(status.age / 1000).toFixed(0)} s ago. Hidden so old masks are not read as the view now.`
              : status.level === 'refused'
                ? status.reason || 'Only the full link carries the segmented view.'
                : 'The OAK detects objects on the camera itself and measures each distance with its stereo depth.'}
          </span>
        </div>
      )}
    </div>
  );
}
