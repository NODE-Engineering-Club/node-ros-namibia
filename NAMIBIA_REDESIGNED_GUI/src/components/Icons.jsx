// Icons exported from the Figma file as SVG (NODE emblem and sidebar icons),
// recoloured to currentColor so CSS controls them.

const stroke = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };

export function Emblem({ className = 'emblem' }) {
  return (
    <svg className={className} viewBox="0 0 58 42" fill="currentColor" aria-label="NODE">
      <path d="M42.4791 8.51769C41.8122 9.2846 41.4479 9.65202 40.5665 10.6639C38.3543 13.2037 34.6555 17.4553 28.9941 17.4553C23.3328 17.4553 19.634 13.2037 17.4217 10.6639C16.6168 9.7395 16.2025 9.31376 15.5092 8.51769C12.9297 5.54043 8.11741 0 0 0V6.26944C5.22358 6.26944 8.5346 10.0865 10.7263 12.6118C11.4108 13.402 11.825 13.8248 12.6418 14.7638C13.3116 15.5336 14.1724 16.5192 15.233 17.5573H0V23.6227H15.2359C14.1754 24.6608 13.3146 25.6493 12.6447 26.4162C11.7868 27.4018 11.402 27.7926 10.7292 28.5682C8.53753 31.0935 5.22358 34.9106 0.0029379 34.9106V41.18C8.12035 41.18 12.9326 35.6396 15.515 32.6623C16.1819 31.8954 16.5462 31.528 17.4276 30.5161C19.6369 27.9763 23.3387 23.7247 29 23.7247C34.6613 23.7247 38.3601 27.9763 40.5724 30.5161C41.3774 31.4405 41.7916 31.8662 42.485 32.6623C45.0703 35.6396 49.8796 41.18 57.9971 41.18V34.9106C52.7735 34.9106 49.4625 31.0935 47.2708 28.5682C46.6039 27.8013 46.2043 27.3931 45.3553 26.4162C44.6854 25.6464 43.8246 24.6608 42.7641 23.6227H58V17.5573H42.7641C43.8246 16.5192 44.6854 15.5307 45.3553 14.7638C46.2132 13.7782 46.598 13.3874 47.2708 12.6118C49.4625 10.0865 52.7764 6.26944 57.9971 6.26944V0C49.8738 0 45.0615 5.54043 42.4791 8.51769Z" />
    </svg>
  );
}

export const IconAlerts = () => (
  <svg viewBox="0 0 20 22" {...stroke}><path d="M8.07 19.8a2 2 0 0 0 3.46 0M1.06 14.13a1 1 0 0 0 .74 1.67h16a1 1 0 0 0 .74-1.67C17.21 12.76 15.8 11.3 15.8 6.8a6 6 0 0 0-12 0c0 4.5-1.41 5.96-2.74 7.33Z" /></svg>
);
export const IconPreflight = () => (
  <svg viewBox="0 0 18 22" {...stroke}><path d="M12.8 2.8h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-12a2 2 0 0 1-2-2v-14a2 2 0 0 1 2-2h2m1 10 2 2 4-4M5.8.8h6a1 1 0 0 1 1 1v2a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1Z" /></svg>
);
export const IconNavigate = () => (
  <svg viewBox="0 0 24 24" {...stroke}><path d="M21.36 1.68 2.36 10.68l8 2 2 8 9-19Z" /></svg>
);
export const IconMission = () => (
  <svg viewBox="0 0 20 22" {...stroke}><path d="M6.8 17.8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 0h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7h8.5m0 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0Z" /></svg>
);
export const IconRepo = () => (
  <svg viewBox="0 0 24 24" {...stroke}><path d="M4.67 8.33v10.09a1.83 1.83 0 0 0 1.83 1.83h11a1.83 1.83 0 0 0 1.83-1.83V8.33M10.17 12h3.66M3.75 3.75h16.5c.5 0 .92.41.92.92v2.75c0 .5-.41.91-.92.91H3.75a.92.92 0 0 1-.92-.91V4.67c0-.51.41-.92.92-.92Z" /></svg>
);

export const IconCheck = () => (<svg viewBox="0 0 16 16" {...stroke}><circle cx="8" cy="8" r="6.5" /><path d="m5.2 8.2 1.9 1.9 3.7-3.9" /></svg>);
export const IconWarn = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 1.8 15 14H1L8 1.8Z" /><path d="M8 6.2v3.6M8 11.8v.1" /></svg>);
export const IconX = () => (<svg viewBox="0 0 16 16" {...stroke}><circle cx="8" cy="8" r="6.5" /><path d="m5.6 5.6 4.8 4.8m0-4.8-4.8 4.8" /></svg>);
export const IconUnknown = () => (<svg viewBox="0 0 16 16" {...stroke}><circle cx="8" cy="8" r="6.5" /><path d="M6.3 6.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.6-.7 1.1v.3M8 11.6v.1" /></svg>);
export const IconShield = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 1.5 2.5 3.5v4c0 3.3 2.4 5.9 5.5 7 3.1-1.1 5.5-3.7 5.5-7v-4L8 1.5Z" /><path d="M8 5.2v3.3M8 10.6v.1" /></svg>);
export const IconRefresh = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M13.5 6.5A5.5 5.5 0 0 0 3.2 4.8M2.5 9.5a5.5 5.5 0 0 0 10.3 1.7" /><path d="M2.6 2.2v2.9h2.9M13.4 13.8v-2.9h-2.9" /></svg>);
export const IconMap = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="m1.5 3.5 4.3-1.7 4.4 1.7 4.3-1.7v10.7l-4.3 1.7-4.4-1.7-4.3 1.7V3.5ZM5.8 1.8v10.7M10.2 3.5v10.7" /></svg>);
export const IconCamera = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M1.5 5.2c0-.7.6-1.2 1.2-1.2h1.8L5.8 2.5h4.4L11.5 4h1.8c.7 0 1.2.5 1.2 1.2v7.1c0 .7-.5 1.2-1.2 1.2H2.7c-.6 0-1.2-.5-1.2-1.2V5.2Z" /><circle cx="8" cy="8.5" r="2.6" /></svg>);
export const IconPulse = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M1 8h3l2-5 4 10 2-5h3" /></svg>);
export const IconFlag = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M3 14.5V1.8m0 .7h9l-2 3.2 2 3.3H3" /></svg>);
export const IconWave = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M1 6c2-2 3-2 5 0s3 2 5 0 3-2 4 0M1 11c2-2 3-2 5 0s3 2 5 0 3-2 4 0" /></svg>);
export const IconUpload = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 10.5V2m0 0L4.8 5.2M8 2l3.2 3.2M2 10.5v2.3c0 .7.5 1.2 1.2 1.2h9.6c.7 0 1.2-.5 1.2-1.2v-2.3" /></svg>);
export const IconPin = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 14.5s5-4.3 5-8.3a5 5 0 0 0-10 0c0 4 5 8.3 5 8.3Z" /><circle cx="8" cy="6.2" r="1.8" /></svg>);
export const IconSend = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M14.5 1.5 7 9M14.5 1.5 10 14.5 7 9 1.5 6l13-4.5Z" /></svg>);
export const IconPlay = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M4 2.5v11l9-5.5-9-5.5Z" /></svg>);
export const IconStop = () => (<svg viewBox="0 0 16 16" {...stroke}><rect x="3" y="3" width="10" height="10" rx="1.5" /></svg>);
export const IconUp = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 13V3M4 7l4-4 4 4" /></svg>);
export const IconDown = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 3v10M4 9l4 4 4-4" /></svg>);
export const IconTrash = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M2.5 4h11M6 4V2.5h4V4m2 0-.6 9.3c0 .7-.6 1.2-1.2 1.2H5.8c-.6 0-1.2-.5-1.2-1.2L4 4" /></svg>);
export const IconPlus = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 3v10M3 8h10" /></svg>);
export const IconSave = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M2.5 2.5h8.5l2.5 2.5v8.5h-11v-11ZM5 2.5v3.5h5V2.5M5 13.5V9.5h6v4" /></svg>);
export const IconDownload = () => (<svg viewBox="0 0 16 16" {...stroke}><path d="M8 2v8.5m0 0L4.8 7.3M8 10.5l3.2-3.2M2 10.5v2.3c0 .7.5 1.2 1.2 1.2h9.6c.7 0 1.2-.5 1.2-1.2v-2.3" /></svg>);
