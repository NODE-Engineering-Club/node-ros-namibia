import { Component } from 'react';

/**
 * MapLibre needs WebGL. Without it (the Jetson's own Chromium, some locked-
 * down laptops) `new maplibregl.Map` throws inside an effect, and with no
 * boundary React unmounted the WHOLE Navigate screen: a blank page, which is
 * worse than any single missing panel. These keep the failure inside the map
 * slot and say what it is; every other panel keeps working.
 */
let cached;
export function hasWebGL() {
  if (cached !== undefined) return cached;
  try {
    const c = document.createElement('canvas');
    cached = Boolean(c.getContext('webgl2') || c.getContext('webgl'));
  } catch {
    cached = false;
  }
  return cached;
}

export function MapUnavailable({ reason }) {
  return (
    <div className="blank map-unavailable" role="status">
      <strong>Map unavailable in this browser</strong>
      <span className="hint">
        {reason || 'The map needs WebGL, which this browser does not provide.'} Open the
        cockpit on a laptop or a browser with hardware acceleration on. Position, heading and
        every other panel are unaffected.
      </span>
    </div>
  );
}

export class MapBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error('Map panel failed:', error);
  }

  render() {
    if (this.state.error) {
      const msg = String(this.state.error?.message || this.state.error);
      const webgl = /webgl/i.test(msg) || !hasWebGL();
      return <MapUnavailable reason={webgl ? undefined : `The map failed to start (${msg.slice(0, 120)}).`} />;
    }
    return this.props.children;
  }
}
