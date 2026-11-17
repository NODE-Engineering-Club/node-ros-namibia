import { createContext, useCallback, useContext, useEffect, useState } from 'react';

/**
 * The simple view, and the "Details" view behind one button.
 *
 * On a beach the cockpit has to answer four questions at a glance: can I go,
 * where is the boat, is everything fine, what do I have to do. Everything
 * else — roll and pitch, satellite counts, relay states, the link budget, the
 * green pre-flight rows — explains rather than asks for action, so it is drawn
 * only in Details. Hidden, never removed: every element is still in the code
 * and one press away.
 *
 * Remembered per browser, so a developer's laptop can stay in Details while
 * the field tablet stays simple. Storage that throws (private window, blocked
 * site data) falls back to the simple view.
 */
const KEY = 'asket.view.details';

export const DetailsContext = createContext(false);

function read() {
  try {
    return window.localStorage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function useDetailsPreference() {
  const [details, setDetails] = useState(read);
  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, details ? '1' : '0');
    } catch {
      // Losing the preference is survivable; failing to render is not.
    }
  }, [details]);
  return [details, useCallback(() => setDetails((v) => !v), [])];
}

/** True when the operator has asked for the detailed view. */
export function useDetails() {
  return useContext(DetailsContext);
}
