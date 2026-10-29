// 20x20 line icons, shared with the SVG mockups in svg/.
const PATHS = {
  home: '<rect x="2" y="2" width="7" height="7" rx="1.5"/><rect x="11" y="2" width="7" height="7" rx="1.5"/><rect x="2" y="11" width="7" height="7" rx="1.5"/><rect x="11" y="11" width="7" height="7" rx="1.5"/>',
  library: '<path d="M10 5C8 3.5 5.5 3 3 3v12c2.5 0 5 .5 7 2 2-1.5 4.5-2 7-2V3c-2.5 0-5 .5-7 2z"/><path d="M10 5v12"/>',
  community: '<circle cx="7" cy="7" r="3"/><path d="M1 17c0-3 2.7-5 6-5s6 2 6 5"/><circle cx="14.5" cy="6" r="2.5"/><path d="M15 11c2.5 0 4.5 1.6 4.5 4.5"/>',
  person: '<circle cx="10" cy="7" r="3.5"/><path d="M3 18c0-3.9 3.1-6.5 7-6.5s7 2.6 7 6.5"/>',
  ai: '<path d="M11 2L4 11h6l-1 7 7-9h-6z"/>',
  sales: '<path d="M2 3h2.5l2 10h9.5l2-7H5.5"/><circle cx="8" cy="16.5" r="1.3"/><circle cx="15" cy="16.5" r="1.3"/>',
  discount: '<path d="M10.5 2H17a1 1 0 0 1 1 1v6.5l-8.5 8.5L2 10.5z"/><circle cx="13.5" cy="6.5" r="1.4"/>',
  publisher: '<path d="M3 18V5l7-3 7 3v13"/><path d="M1.5 18h17M7 8h2M11 8h2M7 12h2M11 12h2"/>',
  employee: '<rect x="2" y="4" width="16" height="13" rx="2"/><circle cx="7.5" cy="10" r="2.2"/><path d="M4 15c.6-1.6 2-2.4 3.5-2.4s2.9.8 3.5 2.4M12.5 9h3.5M12.5 12h3"/>',
  access: '<circle cx="7" cy="12" r="4"/><path d="M10 9l7-7M14 5l2.5 2.5M12 7l2 2"/>',
  settings: '<circle cx="10" cy="10" r="2.6"/><circle cx="10" cy="10" r="6.5"/><path d="M10 1v2M10 17v2M1 10h2M17 10h2M3.6 3.6l1.4 1.4M15 15l1.4 1.4M3.6 16.4L5 15M15 5l1.4-1.4"/>',
  help: '<circle cx="10" cy="10" r="8"/><path d="M7.6 7.8a2.5 2.5 0 1 1 3.4 2.4c-.7.3-1 .8-1 1.6"/><path d="M10 14.6v.1"/>',
  store: '<path d="M2 7l1.5-4.5h13L18 7"/><path d="M2 7c0 1.5 1.3 2.5 2.7 2.5S7.3 8.5 7.3 7c0 1.5 1.3 2.5 2.7 2.5s2.7-1 2.7-2.5c0 1.5 1.3 2.5 2.6 2.5S18 8.5 18 7M3.5 9.5V17h13V9.5M8 17v-4h4v4"/>',
  units: '<path d="M10 2l7 3.5v9L10 18l-7-3.5v-9z"/><path d="M3 5.5l7 3.5 7-3.5M10 9v9"/>',
  money: '<circle cx="10" cy="10" r="8"/><path d="M12.5 7c-.5-1-1.5-1.4-2.5-1.4-1.4 0-2.5.8-2.5 2s1 1.7 2.5 2 2.5.8 2.5 2-1.1 2-2.5 2c-1.1 0-2.1-.5-2.6-1.5M10 4v1.6M10 14.4V16"/>',
  plus: '<path d="M10 4v12M4 10h12"/>',
  chev: '<path d="M6 8l4 4 4-4"/>',
  search: '<circle cx="8.5" cy="8.5" r="6"/><path d="M13 13l5 5"/>',
  edit: '<path d="M13.5 3l3.5 3.5L7 16.5H3.5V13z"/>',
  info: '<circle cx="10" cy="10" r="8"/><path d="M10 9v5M10 6.2v.1"/>',
  cal: '<rect x="2.5" y="4" width="15" height="13.5" rx="2"/><path d="M2.5 8.5h15M6.5 2v4M13.5 2v4"/>',
  back: '<path d="M16 10H4M9 5l-5 5 5 5"/>',
  trash: '<path d="M3 5h14M8 5V3h4v2M5 5l1 12h8l1-12M8.5 8.5v5.5M11.5 8.5v5.5"/>',
  key: '<circle cx="7" cy="12" r="4"/><path d="M10 9l7-7M14 5l2.5 2.5"/>',
  lock: '<rect x="3.5" y="9" width="13" height="9" rx="2"/><path d="M6.5 9V6a3.5 3.5 0 0 1 7 0v3"/>',
  check: '<path d="M4 10.5l4 4 8-9"/>',
  shelf: '<path d="M2 17.5h16M3.5 17.5V4h3v13.5M6.5 17.5V6h3v11.5M11 17.5l-1.2-11 3-.4 1.2 11M15 17.5V5h2.5v12.5"/>',
  eye: '<path d="M1.5 10S4.5 4 10 4s8.5 6 8.5 6-3 6-8.5 6-8.5-6-8.5-6z"/><circle cx="10" cy="10" r="2.5"/>',
  sun: '<circle cx="10" cy="10" r="3.5"/><path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6L16 16M4 16l1.4-1.4M14.6 5.4L16 4"/>',
  moon: '<path d="M16.5 12.5A7 7 0 0 1 7.5 3.5a7 7 0 1 0 9 9z"/>',
  monitor: '<rect x="2" y="3" width="16" height="11" rx="2"/><path d="M7 17.5h6M10 14v3.5"/>',
  table: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M2.5 8h15M2.5 12.2h15M8 8v8.5"/>',
  shield: '<path d="M10 2l6.5 2.5V9c0 4.2-2.8 7.5-6.5 9-3.7-1.5-6.5-4.8-6.5-9V4.5z"/><path d="M7 10l2.2 2.2L13.5 8"/>',
  database: '<ellipse cx="10" cy="4.5" rx="7" ry="2.5"/><path d="M3 4.5v11c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-11M3 10c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
  logout: '<path d="M8 3H4.5A1.5 1.5 0 0 0 3 4.5v11A1.5 1.5 0 0 0 4.5 17H8M13 6l4 4-4 4M17 10H8"/>',
  phone: '<path d="M4 2.5h3.5l1.8 4.3-2.4 1.6a9.6 9.6 0 0 0 4.7 4.7l1.6-2.4 4.3 1.8v3.5a1.8 1.8 0 0 1-1.9 1.8A15.6 15.6 0 0 1 2.2 4.4 1.8 1.8 0 0 1 4 2.5z"/>',
  pin: '<path d="M10 18s6-5.5 6-10.2a6 6 0 0 0-12 0C4 12.5 10 18 10 18z"/><circle cx="10" cy="7.8" r="2.2"/>',
  idcard: '<rect x="1.5" y="4" width="17" height="12" rx="2"/><circle cx="7" cy="9.5" r="2"/><path d="M4 14c.5-1.4 1.6-2 3-2s2.5.6 3 2M12.5 8.5h3.5M12.5 11.5h3"/>',
  homeaddr: '<path d="M3 9l7-6 7 6v8.5H3z"/><path d="M8 17.5V12h4v5.5"/>',
  map: '<path d="M2 5l5-2 6 2 5-2v12l-5 2-6-2-5 2z"/><path d="M7 3v12M13 5v12"/>',
  mail: '<rect x="2" y="4" width="16" height="12" rx="2"/><path d="M2.5 5l7.5 6 7.5-6"/>',
  restore: '<path d="M4 10a6 6 0 1 0 2-4.5"/><path d="M3 3v4h4"/>',
};

export function icon(name, label) {
  const a11y = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true"';
  return `<svg class="i" viewBox="0 0 20 20" ${a11y}>${PATHS[name] || ''}</svg>`;
}

export const LOGO = '<svg width="22" height="22" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 5C8 3.5 5.5 3 3 3v12c2.5 0 5 .5 7 2 2-1.5 4.5-2 7-2V3c-2.5 0-5 .5-7 2z" fill="#fff"/><path d="M10 5v12" stroke="#1f4d3a" stroke-width="1.4"/></svg>';
