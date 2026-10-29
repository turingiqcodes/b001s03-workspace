// API client, formatting and form helpers shared by every screen.
import { icon } from './icons.js';

// ---------------------------------------------------------------- this browser's preferences (Settings)
// Kept in localStorage: they are about this screen, not the account. index.html applies them before first paint.
const PREFS_KEY = 'dl.prefs';
export const PREF_DEFAULTS = { theme: 'system', density: 'comfortable', pageSize: 8 };

export function prefs() {
  try { return { ...PREF_DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return { ...PREF_DEFAULTS }; }
}

export function setPref(key, value) {
  const next = { ...prefs(), [key]: value };
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* applies to this visit only */ }
  applyPrefs(next);
}

export function applyPrefs(p = prefs()) {
  const root = document.documentElement;
  if (p.theme === 'light' || p.theme === 'dark') root.dataset.theme = p.theme; else delete root.dataset.theme;
  if (p.density === 'compact') root.dataset.density = 'compact'; else delete root.dataset.density;
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail || `Request failed (${status})`);
    this.status = status;
    this.errors = body?.errors || {};
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, { detail: 'The server is not reachable. Is it running?' });
  }
  const data = await res.json().catch(() => ({}));
  // The session ended (expired, signed out elsewhere, login disabled): app.js shows the sign-in page.
  if (res.status === 401 && path !== '/api/login') window.dispatchEvent(new Event('signed-out'));
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

// ---------------------------------------------------------------- formatting
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function date(d, short = false) {
  if (!d) return 'Not set';
  const [y, m, day] = String(d).slice(0, 10).split('-');
  return short ? `${MONTHS[m - 1]} ${y}` : `${Number(day)} ${MONTHS[m - 1]} ${y}`;
}
export const money = (v) => (v === null || v === undefined ? '–' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
export const num = (v) => (v === null || v === undefined ? '–' : Number(v).toLocaleString('en-US'));
export const pct = (v) => `${Number(v)}%`;
export const initials = (name) => { const p = String(name).trim().split(/\s+/); return (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase(); };
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export const TYPE_LABEL = { business: 'Business', psychology: 'Psychology', popular_comp: 'Popular computing',
  mod_cook: 'Modern cooking', trad_cook: 'Traditional cooking', UNDECIDED: 'Undecided' };
export const typeLabel = (t) => TYPE_LABEL[String(t).trim()] || t;
const TERMS = { 'Net 30': '', 'Net 60': 'blue', 'ON invoice': 'amber' };
export const terms = (t) => `<span class="pill ${TERMS[t] ?? ''}">${esc(t)}</span>`;

export function byline(name) {
  if (!name) return '';
  const p = name.split(' ');
  return `${p[0][0]}. ${p.slice(1).join(' ')}`;
}

export function cover(t, authorName) {
  return `<div class="cover ${esc(String(t.type || '').trim())}" aria-hidden="true">
    <div class="ct">${esc(t.title || 'Your book title')}</div><div class="rule"></div><div class="cb">${esc(byline(authorName))}</div></div>`;
}

// ---------------------------------------------------------------- page pieces
export function head(title, { count, crumb, back, sub } = {}) {
  return `<div class="page-head">
      ${back ? `<a class="back" href="${back}" aria-label="Back">${icon('back')}</a>` : ''}
      <h1>${esc(title)}${count !== undefined ? ` <span class="count">(${count})</span>` : ''}</h1>
      ${crumb ? `<span class="crumb">${esc(crumb[0])} / <strong>${esc(crumb[1])}</strong></span>` : ''}
    </div>${sub ? `<p class="sub">${esc(sub)}</p>` : ''}`;
}

export function tiles(items) {
  return `<div class="tiles">${items.map(([ic, label, value, note, warn]) => `
    <div class="card tile${warn ? ' warn' : ''}"><div class="ic">${icon(ic)}</div>
      <div class="label">${esc(label)}</div><div class="value" title="${esc(value)}">${esc(value)}</div>
      <div class="tnote">${esc(note)}</div></div>`).join('')}</div>`;
}

export function pager(page, pageSize, total) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total, page * pageSize);
  const buttons = Array.from({ length: pages }, (_, i) => i + 1)
    .map((p) => `<button data-page="${p}" ${p === page ? 'aria-current="page"' : ''} aria-label="Page ${p}">${p}</button>`).join('');
  return `<div class="pager"><span>Showing ${from}–${to} of ${total}</span><div class="pages">
    <button data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="Previous page">‹</button>${buttons}
    <button data-page="${page + 1}" ${page >= pages ? 'disabled' : ''} aria-label="Next page">›</button></div></div>`;
}

export function level(e) {
  const span = e.max_lvl - e.min_lvl;
  const at = Math.min(Math.max(span ? (e.job_lvl - e.min_lvl) / span : 1, 0), 1) * 100;
  const out = !e.level_in_range;
  return `<div class="level${out ? ' out' : ''}" title="Allowed ${e.min_lvl} – ${e.max_lvl}${out ? ' (outside the range)' : ''}">
    <div class="bar"><div class="fill" style="width:${at}%"></div><div class="mark" style="left:${at}%"></div></div>
    <strong>${e.job_lvl}</strong></div>`;
}

export function options(items, value, placeholder) {
  return (placeholder !== undefined ? `<option value="">${esc(placeholder)}</option>` : '')
    + items.map(([v, label]) => `<option value="${esc(v)}" ${String(v) === String(value ?? '') ? 'selected' : ''}>${esc(label)}</option>`).join('');
}

let toastTimer;
export function toast(message, bad = false) {
  const t = document.getElementById('toast');
  t.textContent = message;
  t.classList.toggle('bad', bad);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}

// ---------------------------------------------------------------- forms
// field(name, label, input, {req, hint, w}) -> one labelled field; the API's error for `name` appears under it.
export function field(name, label, input, { req = false, hint = '', w = 3 } = {}) {
  return `<label class="f w${w}" data-field="${name}"><span>${esc(label)}${req ? ' <span class="req">*</span>' : ''}</span>
    ${input}${hint ? `<span class="hint">${esc(hint)}</span>` : ''}<span class="error" hidden></span></label>`;
}

export function formValues(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) {
    const s = String(v).trim();
    out[k] = s === '' ? null : s;
  }
  return out;
}

export function showErrors(form, errors = {}) {
  form.querySelectorAll('[data-field]').forEach((f) => {
    const msg = errors[f.dataset.field];
    f.classList.toggle('has-error', Boolean(msg));
    const el = f.querySelector('.error');
    el.hidden = !msg;
    el.textContent = msg || '';
  });
  const first = form.querySelector('.has-error input, .has-error select, .has-error textarea');
  if (first) first.focus();
}

// submit(form, send, done): runs send(values); shows field errors, or the outcome in the form's .result box.
export function onSubmit(form, send, done) {
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const result = form.querySelector('.result');
    button.disabled = true;
    result.innerHTML = '';
    try {
      const values = formValues(form);
      const out = await send(values);
      showErrors(form, {});
      done(out, values);
    } catch (err) {
      showErrors(form, err.errors);
      const known = Object.keys(err.errors || {}).filter((k) => form.querySelector(`[data-field="${k}"]`));
      const extra = Object.entries(err.errors || {}).filter(([k]) => !known.includes(k)).map(([, m]) => m);
      result.innerHTML = `<div class="note bad">${icon('info')}<div><strong>${esc(err.message)}</strong>
        ${extra.length ? `<br>${extra.map(esc).join('<br>')}` : ''}</div></div>`;
    } finally {
      button.disabled = false;
    }
  });
}

export function savedNote(out, what, backHref, backLabel) {
  const body = out.saved
    ? `<strong>${esc(what)} saved.</strong>`
    : `<strong>All checks passed.</strong> ${esc(what)} would be saved, but practice mode is on, so nothing was stored.`;
  return `<div class="note ok">${icon('check')}<div>${body}<br><a href="${backHref}">${esc(backLabel)}</a></div></div>`;
}
