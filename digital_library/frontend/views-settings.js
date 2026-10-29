// Settings, for both roles. Appearance and table rows are this browser's preferences (lib.js prefs);
// account and system details come from /api/settings.
import { icon } from './icons.js';
import { api, date, esc, field, head, plural, prefs, setPref, showErrors, toast } from './lib.js';

const THEMES = [['light', 'Light', 'sun'], ['dark', 'Dark', 'moon'], ['system', 'Match system', 'monitor']];
const DENSITY = [['comfortable', 'Comfortable'], ['compact', 'Compact']];
const PAGE_SIZES = [8, 15, 25, 50];

const segmented = (name, items, current) => `<div class="segmented" role="group" aria-label="${esc(name)}">${items.map(([value, label, ic]) => `
  <button type="button" data-pref="${esc(name)}" data-value="${esc(value)}" aria-pressed="${String(value) === String(current)}">${ic ? icon(ic) : ''}${esc(label)}</button>`).join('')}</div>`;

const row = (title, note, control) => `<div class="set-row"><div><strong>${esc(title)}</strong>${note ? `<small>${esc(note)}</small>` : ''}</div><div class="value">${control}</div></div>`;

const section = (ic, title, meta, body) => `<section class="card"><header><span class="ic">${icon(ic)}</span><h2>${esc(title)}</h2>${meta ? `<span class="meta">${esc(meta)}</span>` : ''}</header>${body}</section>`;

// ---------------------------------------------------------------- change password (Settings and My Profile)
export function passwordDialog() {
  return `<dialog id="password-dialog"><form method="dialog" novalidate>
      <div class="dbody"><div class="dicon ok">${icon('key')}</div><div><h2>Change your password</h2><p>Other browsers where you are signed in will be signed out.</p>
        <div class="dialog-fields">
          ${field('current_password', 'Current password', '<input name="current_password" type="password" autocomplete="current-password">', { req: true })}
          ${field('password', 'New password', '<input name="password" type="password" autocomplete="new-password">', { req: true, hint: 'At least 5 characters.' })}
          ${field('confirm', 'Type it again', '<input name="confirm" type="password" autocomplete="new-password">', { req: true })}
        </div><div class="result"></div></div></div>
      <footer><button class="btn" type="button" id="password-cancel">Cancel</button><button class="btn primary" type="submit">Change password</button></footer>
    </form></dialog>`;
}

export function mountPasswordDialog(root, opener, done = () => {}) {
  const dlg = root.querySelector('#password-dialog');
  const form = dlg.querySelector('form');
  opener.addEventListener('click', () => {
    form.reset();
    showErrors(form, {});
    form.querySelector('.result').innerHTML = '';
    dlg.showModal();
    form.current_password.focus();
  });
  root.querySelector('#password-cancel').addEventListener('click', () => dlg.close());
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (form.password.value !== form.confirm.value) { showErrors(form, { confirm: 'The two passwords are different.' }); return; }
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api('/api/me/password', { method: 'POST', body: { current_password: form.current_password.value, password: form.password.value } });
      dlg.close();
      toast('Password changed. Other browsers have been signed out.');
      done();
    } catch (err) {
      showErrors(form, err.errors);
      form.querySelector('.result').innerHTML = Object.keys(err.errors || {}).length ? '' : `<div class="note bad">${icon('info')}<div>${esc(err.message)}</div></div>`;
    } finally {
      button.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- the page
export async function settings({ user, reload }) {
  const { account: a, system: sys } = await api('/api/settings');
  const p = prefs();
  const others = a.sessions - 1;

  const appearance = section('sun', 'Appearance', 'Saved in this browser', `
    <div class="theme-previews" role="group" aria-label="Theme">${THEMES.map(([value, label, ic]) => `
      <button type="button" data-pref="theme" data-value="${value}" aria-pressed="${p.theme === value}">
        <span class="mini ${value}" aria-hidden="true"><i></i><i></i></span><span>${icon(ic)} ${esc(label)}</span></button>`).join('')}</div>
    ${row('Density', 'Compact fits more rows on screen.', segmented('density', DENSITY, p.density))}`);

  const tables = user.role === 'admin' ? section('table', 'Tables', 'Saved in this browser',
    row('Rows per page', 'Used by Sales and Employees.', segmented('pageSize', PAGE_SIZES.map((n) => [n, String(n)]), p.pageSize))) : '';

  const account = section('shield', 'Account and security', null, `
    ${row('Username', null, `@${esc(a.username)}`)}
    ${row('Role', user.role === 'admin' ? 'Manages the library and creates author logins.' : 'Can add, edit and delete your own books.', user.role === 'admin' ? 'Admin' : 'Author')}
    ${row('Login created', null, date(a.created_at))}
    ${row('Password', 'Only you know it. The admin can set a new one if you forget it.', `<button class="btn small" type="button" id="change-password">${icon('key')} Change password</button>`)}
    ${row('Signed-in browsers', others > 0 ? `This one and ${plural(others, 'other')}.` : 'Only this browser.',
      `<button class="btn small" type="button" id="end-others" ${others > 0 ? '' : 'disabled'}>${icon('logout')} Sign out other browsers</button>`)}`);

  const system = sys ? section('database', 'System', 'Read-only: set when the server starts', `
    ${row('Database', null, esc(sys.database))}
    ${row('Saving mode', sys.writes ? 'Forms store changes in the pubs tables.' : 'Forms run every database check, then undo it. Start the server with DL_ALLOW_WRITES=1 to store changes.',
      sys.writes ? '<span class="pill dot">Live</span>' : '<span class="pill amber">Practice</span>')}
    ${row('Sign-in length', null, `${sys.session_hours} hours · ${sys.remember_days} days with “Remember me”`)}
    ${row('Wrong-password lockout', null, `${sys.fail_limit} tries, then ${sys.fail_window_minutes} minutes`)}
    ${row('Minimum password length', null, `${sys.password_min} characters`)}
    ${row('Logins', null, `${plural(sys.active_authors, 'active author')} · ${plural(sys.signed_in_users, 'account')} signed in now`)}`) : '';

  return {
    html: `${head('Settings', { crumb: [user.role === 'admin' ? 'Admin' : user.name, 'Settings'], sub: 'How the library looks for you, and your sign-in.' })}
      <div class="settings">${appearance}${tables}${account}${system}</div>${passwordDialog()}`,
    mount(root) {
      root.querySelectorAll('[data-pref]').forEach((b) => b.addEventListener('click', () => {
        const key = b.dataset.pref;
        setPref(key, key === 'pageSize' ? Number(b.dataset.value) : b.dataset.value);
        root.querySelectorAll(`[data-pref="${key}"]`).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      }));
      mountPasswordDialog(root, root.querySelector('#change-password'), reload);
      root.querySelector('#end-others').addEventListener('click', async () => {
        try {
          await api('/api/me/sessions/end-others', { method: 'POST' });
          toast('Other browsers have been signed out.');
          reload();
        } catch (err) { toast(err.message, true); }
      });
    },
  };
}
