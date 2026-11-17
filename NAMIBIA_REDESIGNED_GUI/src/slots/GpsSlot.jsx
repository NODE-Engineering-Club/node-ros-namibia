import { useState } from 'react';

import { MissionMap } from '@asket/panels/MissionMap.jsx';

import '../theme/legacy-map.css';

/**
 * GPS window = slot. The live map is the existing, tested MissionMap from
 * src/asket_gui (vessel, track, plan, coverage, lidar, offline tiles, honest
 * "no tiles" fallback). The redesign only provides the glass frame around it
 * and a dark skin for its overlays (legacy-map.css).
 */
export function GpsSlot({ v, connection }) {
  const [follow, setFollow] = useState(true);
  return (
    <div className="slot gps-slot">
      <MissionMap
        state={v.state}
        connection={connection}
        follow={follow}
        onFollowChange={setFollow}
        showRawLidar={false}
      />
    </div>
  );
}
