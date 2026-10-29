// Shell and router. Routes are hash based (#/sales?page=2) so the backend serves one page.
import { icon, LOGO } from './icons.js';
import { api, applyPrefs, esc, initials, prefs, setPref, toast } from './lib.js';
import * as A from './views-admin.js';
import * as U from './views-author.js';
import { bookForm } from './views-books.js';
import { settings } from './views-settings.js';

const ROUTES = {
  admin: [
    ['/dashboard', A.dashboard], ['/library', A.library], ['/titles/:id', A.titleDetail], ['/titles/:id/edit', bookForm],
    ['/books/new', bookForm], ['/removed', A.removed], ['/sales', A.sales], ['/sales/new', A.newOrder],
    ['/discounts', A.discounts], ['/discounts/new', A.newDiscount], ['/stores', A.stores], ['/publishers', A.publishers],
    ['/publishers/new', A.newPublisher], ['/employees', A.employees], ['/employees/new', A.newEmployee], ['/authors', A.authorAccess], ['/authors/new', A.newAuthor],
    ['/settings', settings],
  ],
  author: [['/my-books', U.myBooks], ['/library', U.library], ['/library/:id', U.libraryBook], ['/my-books/new', bookForm], ['/my-books/:id/edit', bookForm], ['/profile', U.profile], ['/settings', settings]],
};
const HOME = { admin: '/dashboard', author: '/my-books' };
const NAV = {
  admin: [['MENU', [['home', 'Dashboard', '/dashboard'], ['library', 'Library', '/library', ['/titles', '/books', '/removed']],
                    ['community', 'Community'], ['ai', 'AI Intelligence']]],
          ['ADMIN', [['sales', 'Sales', '/sales'], ['discount', 'Discounts', '/discounts'], ['store', 'Stores', '/stores'],
                     ['publisher', 'Publishers', '/publishers'], ['employee', 'Employees', '/employees'], ['access', 'Author Access', '/authors']]]],
  author: [['MENU', [['library', 'My Books', '/my-books', ['/my-books/']], ['shelf', 'Library', '/library'], ['plus', 'Add Book', '/my-books/new'], ['person', 'My Profile', '/profile'],
                     ['community', 'Community'], ['ai', 'AI Intelligence']]]],
};

let me = null;
let lookupCache = null;
let renderCount = 0;   // only the latest render may write to the page
let accounts = [];     // accounts signed in on this browser (the switcher), active first
let adding = false;    // "Add another account": show the sign-in page while still signed in

function parse() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs] = raw.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs || '')) };
}

function match(role, path) {
  for (const [pattern, view] of ROUTES[role]) {
    const names = [];
    const re = new RegExp(`^${pattern.replace(/:(\w+)/g, (_, n) => { names.push(n); return '([^/]+)'; })}$`);
    const m = path.match(re);
    if (m) return { view, params: Object.fromEntries(names.map((n, i) => [n, decodeURIComponent(m[i + 1])])) };
  }
  return null;
}

function activeKey(role, path) {
  let best = null;
  for (const [, items] of NAV[role]) {
    for (const [key, , to, also = []] of items) {
      if (!to) continue;
      if (path === to) return key;
      if (!best && (path.startsWith(`${to}/`) || also.some((p) => path.startsWith(p)))) best = key;
    }
  }
  return best;
}

function sidebar(role, path) {
  const current = activeKey(role, path);
  const groups = NAV[role].map(([label, items]) => `<h2>${label}</h2>${items.map(([key, name, to]) => (to
    ? `<a class="nav" href="#${to}" ${key === current ? 'aria-current="page"' : ''}>${icon(key)}${esc(name)}</a>`
    : `<span class="nav off" title="Not built yet">${icon(key)}${esc(name)}</span>`)).join('')}`).join('');
  const settingsLink = `<a class="nav" href="#/settings" ${path === '/settings' ? 'aria-current="page"' : ''}>${icon('settings')}Settings</a>`;
  return `${groups}<div class="foot">${settingsLink}<span class="nav off" title="Not built yet">${icon('help')}Help</span></div>`;
}

const label = (a) => (a.role === 'admin' ? 'Admin' : a.name);
const avatar = (a, cls = 'avatar') => `<span class="${cls}">${esc(initials(label(a)))}${a.role === 'admin' ? '<span class="badge"></span>' : ''}</span>`;

function accountMenu() {
  const others = accounts.filter((a) => a.username !== me.username);
  return `<div class="am-current">${avatar(me, 'avatar big')}<div><strong>${esc(label(me))}</strong><small>@${esc(me.username)} · ${me.role === 'admin' ? 'Admin' : 'Author'}</small></div></div>
    ${others.length ? `<div class="am-label">Switch to</div>${others.map((a) => `<button type="button" role="menuitem" class="am-item" data-switch="${esc(a.username)}">
      ${avatar(a)}<span><strong>${esc(label(a))}</strong><small>@${esc(a.username)}</small></span></button>`).join('')}` : ''}
    <button type="button" role="menuitem" class="am-item" id="add-account"><span class="am-ic">${icon('plus')}</span><span><strong>Add another account</strong><small>Stay signed in to both and switch any time</small></span></button>
    <div class="am-foot"><button type="button" role="menuitem" class="am-link" id="signout">${icon('logout')} Sign out${others.length ? ` of ${esc(label(me))}` : ''}</button>
      ${others.length ? `<button type="button" role="menuitem" class="am-link" id="signout-all">Sign out of all accounts</button>` : ''}</div>`;
}

function topbar() {
  const shown = label(me);
  return `<a class="brand" href="#${HOME[me.role]}"><span class="logo">${LOGO}</span>Digital Library</a>
    <form class="search" role="search" id="search">${icon('search')}<label class="sr-only" for="q">Search books or authors</label>
      <input id="q" name="q" type="search" value="${esc(parse().path === '/library' ? parse().query.q || '' : '')}"
        placeholder="Search title, ID, author, publisher or type"></form>
    <span class="spacer"></span>
    <div class="theme-switch" role="group" aria-label="Theme">${['light', 'dark'].map((t) => `<button type="button" data-theme-to="${t}"
      aria-label="${t === 'light' ? 'Light' : 'Dark'} theme" aria-pressed="${shownTheme() === t}">${icon(t === 'light' ? 'sun' : 'moon')}</button>`).join('')}</div>
    <div class="account">
      <button type="button" class="viewas account-btn" id="account-btn" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu"
        title="Switch account or sign out">${avatar(me)}<span class="who-am-i"><strong>${esc(shown)}</strong><small>@${esc(me.username)}</small></span>
        ${accounts.length > 1 ? `<span class="count-badge" title="${accounts.length} accounts signed in">${accounts.length}</span>` : ''}${icon('chev')}</button>
      <div class="account-menu" id="account-menu" role="menu" aria-label="Accounts" hidden>${accountMenu()}</div></div>`;
}

// The theme on screen right now ("Match system" resolves to light or dark).
function shownTheme() {
  const { theme } = prefs();
  if (theme === 'light' || theme === 'dark') return theme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function signInPage() {
  const continueAs = accounts.length ? `<div class="continue"><p>${adding ? 'Already signed in here' : 'Continue as'}</p>${accounts.map((a) => `
      <button type="button" class="continue-as" data-switch="${esc(a.username)}">${avatar(a)}<span><strong>${esc(label(a))}</strong><small>@${esc(a.username)}</small></span>${icon('back')}</button>`).join('')}
      <p class="or"><span>${adding ? 'or add another account' : 'or sign in with another account'}</span></p></div>` : '';
  return `<div class="signin"><form class="signin-card" id="signin" novalidate aria-labelledby="signin-title">
      <div class="signin-brand"><span class="logo">${LOGO}</span>Digital Library</div>
      <div><h1 id="signin-title">${adding ? 'Add another account' : 'Sign in'}</h1>
        <p class="lead">${adding ? 'You stay signed in to your other accounts.' : 'Use the username and password your admin gave you.'}</p></div>
      ${continueAs}
      <label class="in"><span class="sr-only">Username</span>${icon('person')}
        <input name="username" placeholder="Username" autocomplete="username" autocapitalize="none" spellcheck="false" required></label>
      <label class="in"><span class="sr-only">Password</span>${icon('lock')}
        <input name="password" type="password" placeholder="Password" autocomplete="current-password" required>
        <button type="button" class="eye" id="show-password" aria-label="Show password" aria-pressed="false">${icon('eye')}</button></label>
      <div class="row"><label class="check"><input type="checkbox" name="remember"> Remember me</label><span class="forgot">Forgot? Ask your admin</span></div>
      <div class="result" role="alert"></div>
      <button class="go" type="submit">Login</button>
      ${adding ? '<button type="button" class="cancel-add" id="cancel-add">Cancel</button>' : ''}
      <p class="foot">Accounts are created by your <strong>admin</strong>.</p>
    </form></div>`;
}

async function showSignIn(mine) {
  try { accounts = await api('/api/accounts'); } catch { accounts = []; }
  if (mine !== renderCount) return;
  const shell = document.querySelector('.shell');
  shell.classList.add('signed-out');
  document.querySelector('.topbar').innerHTML = '';
  document.querySelector('.sidebar').innerHTML = '';
  document.querySelector('main').innerHTML = signInPage();
  document.title = 'Sign in · Digital Library';
  const form = document.getElementById('signin');
  form.username.focus();
  form.querySelectorAll('[data-switch]').forEach((b) => b.addEventListener('click', () => switchTo(b.dataset.switch)));
  document.getElementById('cancel-add')?.addEventListener('click', () => {
    adding = false;
    shell.classList.remove('signed-out');
    render();
  });
  const eye = document.getElementById('show-password');
  eye.addEventListener('click', () => {
    const show = form.password.type === 'password';
    form.password.type = show ? 'text' : 'password';
    eye.setAttribute('aria-pressed', String(show));
    eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const result = form.querySelector('.result');
    if (!form.username.value.trim() || !form.password.value) {
      result.innerHTML = `<div class="note bad">${icon('info')}<div>Enter your username and password.</div></div>`;
      return;
    }
    button.disabled = true;
    result.innerHTML = '';
    try {
      me = await api('/api/login', { method: 'POST',
        body: { username: form.username.value.trim(), password: form.password.value, remember: form.remember.checked } });
      adding = false;
      shell.classList.remove('signed-out');
      await refreshAccounts();
      goHome();
    } catch (err) {
      form.password.value = '';
      form.password.focus();
      result.innerHTML = `<div class="note bad">${icon('info')}<div>${esc(err.message)}</div></div>`;
    } finally {
      button.disabled = false;
    }
  });
}

async function lookups() {
  if (!lookupCache) lookupCache = api('/api/lookups');
  return lookupCache;
}

async function render() {
  const main = document.querySelector('main');
  const { path, query } = parse();
  const mine = ++renderCount;
  if (!me || adding) { showSignIn(mine); return; }
  document.querySelector('.shell').classList.remove('signed-out');
  document.querySelector('.topbar').innerHTML = topbar();
  wireTopbar();
  const found = match(me.role, path);
  if (!found) { location.replace(`#${HOME[me.role]}`); return; }
  document.querySelector('.sidebar').innerHTML = sidebar(me.role, path);
  main.innerHTML = `${me.writes ? '' : `<div class="practice">${icon('info')}<span><strong>Practice mode.</strong> Saving a form runs every database check, then undoes it, so nothing in the pubs tables changes. Deleting and restoring books works for real.</span></div>`}<div class="loading" role="status">Loading…</div>`;
  const ctx = { params: found.params, query, user: me, lookups, go: (h) => { location.hash = h; }, reload: render };

  try {
    const view = await found.view(ctx);
    if (mine !== renderCount) return;   // the user moved on (or signed out) while this was loading
    main.querySelector('.loading').outerHTML = view.html;
    view.mount?.(main);
    document.title = `${main.querySelector('h1')?.textContent.trim() || 'Digital Library'} · Digital Library`;
  } catch (err) {
    if (mine !== renderCount) return;
    main.querySelector('.loading').outerHTML = `<div class="card error-page body" style="padding:28px"><h1 style="margin-top:0">Something went wrong</h1>
      <p>${esc(err.message)}</p><a class="btn" href="#${HOME[me.role]}">Go to the start page</a></div>`;
  }
  main.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function wireTopbar() {
  document.querySelectorAll('[data-theme-to]').forEach((b) => b.addEventListener('click', () => {
    setPref('theme', b.dataset.themeTo);
    document.querySelectorAll('[data-theme-to]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    document.querySelectorAll('[data-pref="theme"]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.value === b.dataset.themeTo)));   // Settings, if open
  }));
  const btn = document.getElementById('account-btn');
  const menu = document.getElementById('account-menu');
  btn.addEventListener('click', async () => {
    if (!menu.hidden) { closeMenu(); return; }
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    await refreshAccounts();              // someone may have signed in or out in another tab
    if (menu.hidden) return;
    menu.innerHTML = accountMenu();
    wireMenu(menu);
    menu.querySelector('[role="menuitem"]')?.focus();
  });
  wireMenu(menu);
  document.getElementById('search').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const q = ev.target.q.value.trim();
    const target = `#/library${q ? `?q=${encodeURIComponent(q)}` : ''}`;   // both roles have a Library
    if (location.hash === target) render(); else location.hash = target;
  });
}

function closeMenu(focusButton = false) {
  const menu = document.getElementById('account-menu');
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  const btn = document.getElementById('account-btn');
  btn.setAttribute('aria-expanded', 'false');
  if (focusButton) btn.focus();
}

function wireMenu(menu) {
  menu.querySelectorAll('[data-switch]').forEach((b) => b.addEventListener('click', () => switchTo(b.dataset.switch)));
  menu.querySelector('#add-account').addEventListener('click', () => { closeMenu(); adding = true; render(); });
  menu.querySelector('#signout').addEventListener('click', () => signOut(false));
  menu.querySelector('#signout-all')?.addEventListener('click', () => signOut(true));
}

async function refreshAccounts() {
  try { accounts = await api('/api/accounts'); } catch { /* keep the last list */ }
}

function goHome() {
  lookupCache = null;
  const home = `#${HOME[me.role]}`;
  if (location.hash === home) render(); else location.hash = home;
}

async function switchTo(username) {
  try {
    me = await api('/api/accounts/switch', { method: 'POST', body: { username } });
  } catch (err) {
    toast(err.message, true);
    await refreshAccounts();
    render();
    return;
  }
  adding = false;
  document.querySelector('.shell').classList.remove('signed-out');
  await refreshAccounts();
  toast(`Switched to ${label(me)}.`);
  goHome();
}

async function signOut(all) {
  const was = me;
  let next = null;
  try { next = (await api('/api/logout', { method: 'POST', body: all ? { all: true } : undefined })).me; } catch { /* the cookie is gone either way */ }
  me = next;
  await refreshAccounts();
  if (me) {
    toast(`Signed out of ${label(was)}. You're now using ${label(me)}.`);
    goHome();
  } else {
    lookupCache = null;
    history.replaceState(null, '', location.pathname);
    render();
  }
}

async function start() {
  applyPrefs();
  try {
    me = await api('/api/me');
    await refreshAccounts();
  } catch (err) {
    if (err.status !== 401) {
      document.querySelector('main').innerHTML = `<div class="card error-page body" style="padding:28px"><h1 style="margin-top:0">Can’t reach the API</h1><p>${esc(err.message)}</p></div>`;
      return;
    }
  }
  window.addEventListener('hashchange', () => { if (me) render(); });
  document.addEventListener('click', (ev) => { if (!ev.target.closest('.account')) closeMenu(); });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeMenu(true); });
  window.addEventListener('signed-out', () => {
    if (!me) return;
    me = null;
    lookupCache = null;
    toast('You have been signed out. Please sign in again.', true);
    render();
  });
  render();
}

start();
