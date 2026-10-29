// Author screens: only the logged-in author's own books and details.
import { icon } from './icons.js';
import { api, cover, date, esc, head, initials, money, num, pct, toast, typeLabel } from './lib.js';
import { mountPasswordDialog, passwordDialog } from './views-settings.js';

export async function myBooks({ user, reload }) {
  const books = await api('/api/my/books');
  const sold = books.reduce((n, b) => n + b.units_sold, 0);
  const ytd = books.filter((b) => b.ytd_sales !== null);
  const pubs = [...new Set(books.map((b) => b.pub_name).filter(Boolean))];
  const role = (b) => `${b.author_count === 1 ? 'Sole author' : `Author ${b.au_ord} of ${b.author_count}`} · ${b.royaltyper}% royalty share`;
  return {
    html: `<div class="page-head"><h1>My Books <span class="count">(${books.length})</span></h1><span class="crumb">${esc(user.name)} / <strong>My Books</strong></span></div>
      <p class="sub">Books where you are listed as an author. You can add new books, edit them, or delete your own.</p>
      <div class="toolbar"><span class="spacer"></span><a class="btn primary" href="#/my-books/new">${icon('plus')} Add Book</a></div>
      <div class="tiles">
        ${[['library', 'My books', num(books.length), books.every((b) => b.author_count === 1) ? 'you are the only author' : 'including co-authored'],
           ['units', 'Units sold', num(sold), sold ? 'from the sales table' : 'no sales recorded yet'],
           ['money', 'YTD sales', ytd.length ? num(ytd.reduce((n, b) => n + b.ytd_sales, 0)) : '–', ytd.length ? 'titles.ytd_sales' : 'not reported yet'],
           ['publisher', 'Publishers', num(pubs.length), pubs.join(', ') || '–']].map(([ic, l, v, n]) => `<div class="card tile"><div class="ic">${icon(ic)}</div><div class="label">${l}</div><div class="value">${esc(v)}</div><div class="tnote">${esc(n)}</div></div>`).join('')}
      </div>
      <div class="my-books">
        ${books.map((b) => `<section class="card my-book"><div class="shelf">${cover(b, user.name)}</div>
          <div><h3>${esc(b.title)}</h3><span class="muted" style="font-size:12px;font-weight:600">${esc(b.title_id)}</span>
            <dl class="facts"><dt>Type</dt><dd>${esc(typeLabel(b.type))}</dd><dt>Publisher</dt><dd>${esc(b.pub_name || '–')}</dd><dt>Price</dt><dd>${b.price === null ? 'Not priced' : money(b.price)}</dd>
              <dt>Published</dt><dd>${date(b.pubdate)}</dd><dt>YTD sales</dt><dd>${num(b.ytd_sales)}</dd></dl>
            ${b.notes ? `<p class="sub" style="font-style:italic;margin:10px 0 0">“${esc(b.notes)}”</p>` : ''}</div>
          <div class="foot"><span><span class="muted">Role</span> <strong>${esc(role(b))}</strong></span>
            <span class="actions"><a class="btn small" href="#/my-books/${esc(b.title_id)}/edit">${icon('edit')} Edit</a>
            <button class="btn small danger" data-delete="${esc(b.title_id)}" data-title="${esc(b.title)}">${icon('trash')} Delete</button></span></div></section>`).join('')}
        <a class="add-card" href="#/my-books/new" style="text-decoration:none"><div class="plus">${icon('plus')}</div><h3>Add a new book</h3><span>It will be linked to you as the author.</span></a>
      </div>
      <div class="note warn" style="margin-top:16px">${icon('info')}<div><strong>Deleting a book removes it from your list and the library.</strong> Its sales and royalty records are kept, and only the admin can bring it back.</div></div>
      <dialog id="confirm"><div class="dbody"><div class="dicon">${icon('trash')}</div><div><h2 id="confirm-title"></h2>
        <p>It is removed from your list and from the library. Its sales and royalty records are kept, and the admin can restore the book from Removed books.</p></div></div>
        <footer><button class="btn" id="cancel">Cancel</button><button class="btn danger solid" id="ok">Delete book</button></footer></dialog>`,
    mount(root) {
      const dlg = root.querySelector('#confirm');
      let target = null;
      root.querySelectorAll('[data-delete]').forEach((b) => b.addEventListener('click', () => {
        target = b.dataset.delete;
        root.querySelector('#confirm-title').textContent = `Delete “${b.dataset.title}”?`;
        dlg.showModal();
      }));
      root.querySelector('#cancel').addEventListener('click', () => dlg.close());
      root.querySelector('#ok').addEventListener('click', async () => {
        try {
          await api(`/api/titles/${encodeURIComponent(target)}/remove`, { method: 'POST' });
          dlg.close();
          toast('Book deleted. The admin can restore it from Removed books.');
          reload();
        } catch (err) { dlg.close(); toast(err.message, true); }
      });
    },
  };
}

// ---------------------------------------------------------------- library: every author's books, read-only
const libHref = (query) => {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v));
  return `#/library${q.toString() ? `?${q}` : ''}`;
};

export async function library({ query, go }) {
  const d = await api(`/api/library${libHref({ q: query.q, type: query.type }).slice('#/library'.length)}`);
  const chips = [['', `All ${d.total}`], ...d.by_type.map((x) => [x.type, `${typeLabel(x.type).replace('Popular computing', 'Computing')} ${x.n}`])];
  const yours = d.rows.filter((t) => t.mine).length;
  return {
    html: `<div class="page-head"><h1>Library <span class="count">(${d.total})</span></h1><span class="crumb"><span class="pill">${icon('eye')} View only</span></span></div>
      ${query.q ? '' : `<p class="sub">Every book in the library, from every author. Open one to read about it.${yours ? ' Books tagged <strong>Yours</strong> can be edited from My Books.' : ''}</p>`}
      ${query.q ? `<p class="sub">Showing books matching “${esc(query.q)}”. <a href="${libHref({ type: query.type })}">Clear search</a></p>` : ''}
      <div class="chips" role="group" aria-label="Filter by type">${chips.map(([v, l]) => `<button class="chip" data-type="${v}" aria-pressed="${(query.type || '') === v}">${esc(l)}</button>`).join('')}</div>
      ${d.rows.length ? `<div class="books">${d.rows.map((t) => `<a class="book" href="#/library/${esc(t.title_id)}"><div class="shelf">${t.mine ? '<span class="pill dot yours">Yours</span>' : ''}${cover(t, t.authors[0])}</div>
          <div class="t" title="${esc(t.title)}">${esc(t.title)}</div>
          <div class="by"><span>${esc(t.authors.join(', ') || 'No author linked')}</span><strong>${money(t.price)}</strong></div></a>`).join('')}</div>`
        : '<div class="card empty"><h3>No books match</h3>Try another type or search.</div>'}`,
    mount(root) {
      root.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => go(libHref({ q: query.q, type: b.dataset.type }))));
    },
  };
}

export async function libraryBook({ params }) {
  const t = await api(`/api/library/${encodeURIComponent(params.id)}`);
  return {
    html: `${head(t.title, { back: '#/library', crumb: ['Library', t.title_id], sub: `${typeLabel(t.type)} · ${t.pub_name || 'No publisher'} · Title ID ${t.title_id}` })}
      <div class="note ${t.mine ? 'ok' : 'plain'}" style="margin-bottom:16px">${icon(t.mine ? 'check' : 'eye')}<div>${t.mine
        ? `This is your book. <a href="#/my-books/${esc(t.title_id)}/edit">Edit it from My Books</a>.`
        : 'View only. Each author manages their own books; sales and royalties are private to the admin.'}</div></div>
      <div class="detail">
        <section class="card body" style="padding:20px">
          <div class="shelf" style="background:var(--mint);border-radius:12px;height:196px;display:grid;place-items:center">${cover(t, t.authors[0])}</div>
          <dl class="facts"><dt>Type</dt><dd>${esc(typeLabel(t.type))}</dd><dt>Publisher</dt><dd>${esc(t.pub_name || '–')}</dd>
            <dt>Price</dt><dd>${t.price === null ? 'Not priced' : money(t.price)}</dd><dt>Published</dt><dd>${date(t.pubdate)}</dd></dl>
        </section>
        <div class="stack">
          <section class="card"><header><h2>${t.authors.length === 1 ? 'Author' : 'Authors'}</h2><span class="meta">${t.authors.length}</span></header><div class="body">
            ${t.authors.map((n) => `<div class="list-row"><div class="ic"><span class="mini-avatar" style="margin:0">${esc(initials(n))}</span></div><div class="text"><strong>${esc(n)}</strong></div></div>`).join('')
              || '<p class="sub">No author is linked to this book.</p>'}</div></section>
          <section class="card"><header><h2>About this book</h2></header><div class="body"><p style="margin:0">${esc(t.notes || 'No description yet.')}</p></div></section>
        </div>
      </div>`,
  };
}

export async function profile({ user }) {
  const [p, books] = await Promise.all([api('/api/my/profile'), api('/api/my/books')]);
  const info = [['person', 'First name', p.au_fname], ['person', 'Last name', p.au_lname], ['idcard', 'Author ID', p.au_id], ['phone', 'Phone', p.phone],
    ['homeaddr', 'Address', p.address], ['pin', 'City', p.city], ['map', 'State', p.state], ['mail', 'ZIP code', p.zip]];
  return {
    html: `${head('My Profile', { crumb: [user.name, 'My Profile'], sub: 'Your details as stored in the authors table. Ask your admin to change them.' })}
      <div class="profile">
        <section class="card who-card"><div class="banner"></div><div class="big-avatar">${initials(user.name)}</div>
          <h2 style="margin:0;font-size:20px">${esc(user.name)}</h2><p class="sub" style="margin:4px 0 10px">@${esc(p.username)} · Author</p>
          <span class="pill ${p.contract ? '' : 'red'}">${p.contract ? 'Contract signed' : 'No contract'}</span>
          <dl class="facts" style="padding:0 24px;text-align:left"><dt>Books</dt><dd>${books.length}</dd><dt>Units sold</dt><dd>${num(books.reduce((n, b) => n + b.units_sold, 0))}</dd>
            <dt>Login created</dt><dd>${date(p.created_at)}</dd><dt>Last login</dt><dd>${p.last_login ? date(p.last_login) : 'Never'}</dd></dl>
          <button class="btn" id="change-password" type="button">${icon('key')} Change password</button></section>
        <div class="stack">
          <section class="card"><header><h2>Personal information</h2><span class="link muted">${icon('lock')} Managed by admin</span></header>
            <div class="info-grid">${info.map(([ic, l, v]) => `<div><span class="ic">${icon(ic)}</span><span><small>${l}</small><strong>${esc(v || '–')}</strong></span></div>`).join('')}</div></section>
          <section class="card"><header><h2>My books</h2><a class="link" href="#/my-books">View all →</a></header><div class="body">
            ${books.map((b) => `<div class="list-row"><div class="ic">${icon('library')}</div><div class="text"><strong>${esc(b.title)}</strong><small>${esc(b.title_id)} · ${esc(typeLabel(b.type))} · ${esc(b.pub_name || '–')}</small></div><div class="end">${pct(b.royaltyper)}</div></div>`).join('') || '<p class="sub">No books yet.</p>'}
          </div></section>
        </div>
      </div>
      ${passwordDialog()}`,
    mount(root) { mountPasswordDialog(root, root.querySelector('#change-password')); },
  };
}
