import { useState } from 'react';

import { Background } from './Shell.jsx';
import { cloudConfigFromLink, forgetCloudConfig, saveCloudConfig } from '../lib/cloudConfig.js';

/** Shown by the cloud build when it has no Azure access configured yet. */
export function CloudSignIn() {
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const go = () => {
    const cfg = cloudConfigFromLink(text);
    if (!cfg) {
      setError('That link has no blob / sas part. Paste the whole viewer link.');
      return;
    }
    saveCloudConfig(cfg);
    window.location.hash = 'preflight';
    window.location.reload();
  };
  return (
    <>
      <Background />
      <div className="cloud-signin glass">
        <h1>ASKET cockpit · cloud view</h1>
        <p>
          Paste the viewer link from <code>cloud/azure/provision.py</code>. It carries a
          read-only token for the boat&rsquo;s telemetry and is remembered in this browser only.
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="https://…/#blob=…&container=telemetry&asv=ASKET_02&sas=…"
          aria-label="Viewer link"
        />
        <div className="cloud-signin-row">
          <button type="button" className="btn" onClick={go}>Open cockpit</button>
          {error && <span className="cloud-error">{error}</span>}
        </div>
      </div>
    </>
  );
}

/**
 * Always on screen in the cloud build: this is a replay of what the boat
 * sends, through Azure, and nothing here can command the boat.
 */
export function CloudBadge({ connection }) {
  const cfg = connection.cloud || {};
  const signOut = () => {
    forgetCloudConfig();
    window.location.hash = '';
    window.location.reload();
  };
  return (
    <div className="cloud-badge glass" role="status">
      <span className="pill info"><span className="dot" />Cloud view · read-only</span>
      <span className="faint">{cfg.asv} via Azure · data a few seconds behind the boat</span>
      {cfg.from !== 'build' && (
        <button type="button" className="btn small ghost" onClick={signOut}>Sign out</button>
      )}
    </div>
  );
}
