// React is loaded as a UMD global (no build step); htm gives JSX-like templates.
/* global React, ReactDOM, htm */
export const { useState, useEffect, useRef, useMemo, useCallback } = React;
export const html = htm.bind(React.createElement);

export function mount(App) {
  ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
}
