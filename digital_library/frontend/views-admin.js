// Admin screens. Each view returns { html, mount(root) } and gets ctx = { params, query, go, lookups }.
import { icon } from './icons.js';
import {
  ApiError, api, cover, date, esc, field, head, initials, level, money, num, onSubmit, options, pager, pct, plural,
  prefs, savedNote, showErrors, terms, tiles, toast, typeLabel,
} from './lib.js';

const href = (path, query) => {
  const q = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== null && v !== undefined && v !== ''));
  return `#${path}${q.toString() ? `?${q}` : ''}`;
};

function salesTable(rows, { full = false } = {}) {
  if (!rows.length) return '<div class="empty">No sales match.</div>';
  return `<div class="table-wrap"><table><thead><tr><th>Order #</th><th>Date</th><th>Store</th><th>Title</th><th class="num">Qty</th>
      ${full ? '<th class="num">Price</th><th class="num">Amount</th><th>Terms</th>' : ''}</tr></thead><tbody>
    ${rows.map((s) => `<tr><td><strong>${esc(s.ord_num)}</strong></td><td class="muted">${date(s.ord_date)}</td><td>${esc(s.stor_name)}</td>
      <td class="clip"><a href="#/titles/${esc(s.title_id)}" title="${esc(s.title)}">${esc(s.title)}</a></td><td class="num"><strong>${s.qty}</strong></td>
      ${full ? `<td class="num muted">${money(s.price)}</td><td class="num"><strong>${money(s.amount)}</strong></td><td>${terms(s.payterms)}</td>` : ''}</tr>`).join('')}
    </tbody></table></div>`;
}

const scope = (d) => `${d.applies_to}${d.lowqty !== null ? ` · Qty ${num(d.lowqty)} – ${num(d.highqty)}` : ' · Any quantity'}`;

// ---------------------------------------------------------------- dashboard
export async function dashboard() {
  const d = await api('/api/dashboard');
  const t = d.totals;
  return {
    html: `${head('Admin Dashboard', { crumb: ['Admin', 'Dashboard'], sub: 'Welcome back. Here’s how sales, discounts, publishers and employees look today.' })}
      ${tiles([['sales', 'Orders', num(t.orders), `${t.order_lines} order lines · ${t.stores} stores`],
               ['money', 'Revenue', money(t.revenue), `${num(t.units)} units sold`],
               ['publisher', 'Publishers', num(t.publishers), `${t.titles} titles published`],
               ['employee', 'Employees', num(t.employees), `across ${t.employers} publishers`]])}
      <div class="grid-2">
        <section class="card"><header><h2>Recent sales</h2><span class="meta">${t.order_lines} lines</span><a class="link" href="#/sales">View all sales →</a></header>
          ${salesTable(d.recent_sales)}</section>
        <div class="stack">
          <section class="card"><header><h2>Discounts</h2><span class="meta">${d.discount_count}</span><a class="link" href="#/discounts">View all →</a></header>
            <div class="body">${d.discounts.map((x) => `<div class="list-row"><div class="ic">${icon('discount')}</div>
              <div class="text"><strong>${esc(x.discounttype)}</strong><small>${esc(scope(x))}</small></div><div class="end">${pct(x.discount)}</div></div>`).join('')}</div></section>
          <section class="card"><header><h2>Publishers</h2><a class="link" href="#/publishers">View all →</a></header>
            <table><thead><tr><th>Publisher</th><th class="num">Titles</th><th class="num">Staff</th></tr></thead><tbody>
            ${d.publishers.map((p) => `<tr><td>${esc(p.pub_name)}</td><td class="num"><strong>${p.titles}</strong></td><td class="num"><strong>${p.staff}</strong></td></tr>`).join('')}
            </tbody></table></section>
        </div>
      </div>`,
  };
}

// ---------------------------------------------------------------- library and title detail
export async function library({ query, go }) {
  const d = await api(`/api/titles${href('', { q: query.q, type: query.type }).slice(1)}`);
  const chips = [['', `All ${d.total}`], ...d.by_type.map((x) => [x.type, `${typeLabel(x.type).replace('Popular computing', 'Computing')} ${x.n}`])];
  return {
    html: `<div class="page-head"><h1>Library <span class="count">(${d.total})</span></h1>
        <span class="crumb"></span><a class="btn" href="#/removed">${icon('trash')} Removed books</a><a class="btn primary" href="#/books/new">${icon('plus')} Add Book</a></div>
      ${query.q ? `<p class="sub">Showing books matching “${esc(query.q)}”. <a href="${href('/library', { type: query.type })}">Clear search</a></p>` : '<p class="sub">Click a book to see its authors, royalty tiers and sales.</p>'}
      <div class="chips" role="group" aria-label="Filter by type">${chips.map(([v, l]) => `<button class="chip" data-type="${v}" aria-pressed="${(query.type || '') === v}">${esc(l)}</button>`).join('')}</div>
      ${d.rows.length ? `<div class="books">${d.rows.map((t) => {
        const names = t.authors.map((a) => a.name);
        return `<a class="book" href="#/titles/${esc(t.title_id)}"><div class="shelf">${cover(t, names[0])}</div>
          <div class="t" title="${esc(t.title)}">${esc(t.title)}</div>
          <div class="by"><span>${esc(names.join(', ') || 'No author linked')}</span><strong>${money(t.price)}</strong></div></a>`;
      }).join('')}</div>` : '<div class="card empty"><h3>No books match</h3>Try another type or search.</div>'}`,
    mount(root) {
      root.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => go(href('/library', { q: query.q, type: b.dataset.type }))));
    },
  };
}

export async function titleDetail({ params, go }) {
  const d = await api(`/api/titles/${encodeURIComponent(params.id)}`);
  const t = d.title;
  const s = d.summary;
  const first = d.authors[0]?.name;
  return {
    html: `${head(t.title, { back: '#/library', crumb: ['Library', t.title_id], sub: `${typeLabel(t.type)} · ${t.pub_name || 'No publisher'} · Title ID ${t.title_id}` })}
      ${t.removed ? `<div class="note warn" style="margin-bottom:16px">${icon('info')}<div>This book was removed by its author and is hidden from the library. <a href="#/removed">Restore it from Removed books</a>.</div></div>` : ''}
      <div class="detail">
        <section class="card body" style="padding:20px">
          <div class="shelf" style="background:var(--mint);border-radius:12px;height:196px;display:grid;place-items:center">${cover(t, first)}</div>
          <dl class="facts"><dt>Type</dt><dd>${esc(typeLabel(t.type))}</dd><dt>Publisher</dt><dd>${esc(t.pub_name || '–')}</dd>
            <dt>Price</dt><dd>${t.price === null ? 'Not priced' : money(t.price)}</dd><dt>Advance</dt><dd>${t.advance === null ? '–' : money(t.advance)}</dd>
            <dt>Royalty</dt><dd>${t.royalty === null ? '–' : pct(t.royalty)}</dd><dt>YTD sales</dt><dd>${num(t.ytd_sales)}</dd><dt>Published</dt><dd>${date(t.pubdate)}</dd></dl>
          <div class="panel" style="padding:12px 14px"><b style="font-size:10px;letter-spacing:1px;color:var(--muted)">NOTES</b><div style="font-size:13px">${esc(t.notes || 'No notes.')}</div></div>
          <div style="display:flex;gap:12px;margin-top:16px"><a class="btn" href="#/titles/${esc(t.title_id)}/edit">${icon('edit')} Edit</a>
            ${t.removed ? '' : `<button class="btn danger" id="remove">${icon('trash')} Remove</button>`}</div>
        </section>
        <div class="stack">
          <section class="card"><header><h2>Authors</h2><span class="meta">${d.authors.length}</span></header>
            ${d.authors.length ? `<table><thead><tr><th>Author</th><th>Author ID</th><th class="num">Order</th><th class="num">Royalty share</th></tr></thead><tbody>
              ${d.authors.map((a) => `<tr><td><strong>${esc(a.name)}</strong></td><td class="muted">${esc(a.au_id)}</td><td class="num">${a.au_ord}</td><td class="num"><strong>${pct(a.royaltyper)}</strong></td></tr>`).join('')}
              </tbody></table>
              ${d.share_total !== 100 ? `<div class="body" style="padding-top:12px"><div class="note warn">${icon('info')}<div>Royalty shares add up to ${d.share_total}%, not 100%. This needs fixing in titleauthor.</div></div></div>` : ''}`
              : `<div class="body"><div class="note warn">${icon('info')}<div>No author is linked to this title, so no author login can manage it.</div></div></div>`}
          </section>
          <div class="grid-2" style="grid-template-columns:1fr 1fr">
            <section class="card"><header><h2>Royalty schedule</h2></header><div class="body">
              ${d.tiers.length ? d.tiers.map((r) => `<div class="list-row"><div class="text">${num(r.lorange)} ${r.hirange === null ? 'and up' : `– ${num(r.hirange)}`} copies</div><div class="end">${pct(r.royalty)}</div></div>`).join('')
                : '<p class="sub">No royalty tiers for this title.</p>'}</div></section>
            <section class="card"><header><h2>Sales</h2></header><div class="body stat-grid">
              <div><strong>${num(s.units)}</strong><span>Units sold</span></div><div><strong>${s.revenue === null ? '–' : money(s.revenue)}</strong><span>Revenue</span></div>
              <div><strong>${s.orders}</strong><span>Orders</span></div><div><strong>${s.stores}</strong><span>Stores</span></div></div></section>
          </div>
          <section class="card"><header><h2>Sales of this title</h2><span class="meta">${plural(d.sales.length, 'line')}</span></header>
            ${d.sales.length ? `<table><thead><tr><th>Order #</th><th>Date</th><th>Store</th><th class="num">Qty</th><th class="num">Amount</th></tr></thead><tbody>
              ${d.sales.map((x) => `<tr><td><strong>${esc(x.ord_num)}</strong></td><td class="muted">${date(x.ord_date)}</td><td>${esc(x.stor_name)}</td><td class="num">${x.qty}</td><td class="num"><strong>${money(x.amount)}</strong></td></tr>`).join('')}
              </tbody></table>` : '<div class="body"><p class="sub">No sales recorded for this title yet.</p></div>'}</section>
        </div>
      </div>
      <dialog id="confirm"><div class="dbody"><div class="dicon">${icon('trash')}</div><div><h2>Remove “${esc(t.title)}”?</h2>
        <p>It is hidden from the library and from its authors. Its sales and royalty records are kept, and you can restore it from Removed books.</p></div></div>
        <footer><button class="btn" value="cancel" id="cancel">Cancel</button><button class="btn danger solid" id="ok">Remove book</button></footer></dialog>`,
    mount(root) {
      const dlg = root.querySelector('#confirm');
      root.querySelector('#remove')?.addEventListener('click', () => dlg.showModal());
      root.querySelector('#cancel').addEventListener('click', () => dlg.close());
      root.querySelector('#ok').addEventListener('click', async () => {
        try {
          await api(`/api/titles/${encodeURIComponent(t.title_id)}/remove`, { method: 'POST' });
          dlg.close();
          toast(`“${t.title}” removed. You can restore it here.`);
          go('#/removed');
        } catch (err) { dlg.close(); toast(err.message, true); }
      });
    },
  };
}

export async function removed({ reload }) {
  const rows = await api('/api/removals');
  return {
    html: `${head('Removed books', { count: rows.length, back: '#/library', crumb: ['Library', 'Removed books'], sub: 'Books an author deleted. They stay hidden until you restore them.' })}
      <div class="note info" style="margin-bottom:16px">${icon('info')}<div>Removing a book only adds a row to app_title_removals. Its rows in titles, sales and roysched are never deleted, so restoring brings it straight back.</div></div>
      <section class="card">${rows.length ? `<table><thead><tr><th>Title</th><th>Title ID</th><th>Removed by</th><th>Removed on</th><th></th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><strong>${esc(r.title)}</strong></td><td class="muted">${esc(r.title_id)}</td><td>${esc(r.removed_by)}</td><td class="muted">${date(r.removed_at)}</td>
          <td class="num"><button class="btn small" data-restore="${esc(r.title_id)}">${icon('restore')} Restore</button></td></tr>`).join('')}</tbody></table>`
        : `<div class="empty"><div class="big">${icon('library')}</div><h3>No books have been removed</h3>When an author deletes one of their books it shows up here, and you can restore it with one click.</div>`}</section>`,
    mount(root) {
      root.querySelectorAll('[data-restore]').forEach((b) => b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          await api(`/api/removals/${encodeURIComponent(b.dataset.restore)}`, { method: 'DELETE' });
          toast('Book restored. It is back in the library.');
          reload();
        } catch (err) { b.disabled = false; toast(err.message, true); }
      }));
    },
  };
}

// ---------------------------------------------------------------- sales
export async function sales({ query, go, lookups }) {
  const page = Number(query.page || 1);
  const [d, lk] = await Promise.all([api(`/api/sales${href('', { stor_id: query.stor_id, payterms: query.payterms, page, page_size: prefs().pageSize }).slice(1)}`), lookups()]);
  const t = d.totals;
  return {
    html: `${head('Sales', { count: t.lines, crumb: ['Admin', 'Sales'], sub: 'Every order line placed by a store, newest first.' })}
      <div class="toolbar"><label class="sr-only" for="f-store">Store</label><select id="f-store" class="control">${options(lk.stores.map((s) => [s.stor_id, `${s.stor_name} (${s.stor_id})`]), query.stor_id, 'All stores')}</select>
        <label class="sr-only" for="f-terms">Pay terms</label><select id="f-terms" class="control">${options(lk.payterms.map((p) => [p, p]), query.payterms, 'All pay terms')}</select>
        <span class="spacer"></span><a class="btn primary" href="#/sales/new">${icon('plus')} New order</a></div>
      ${tiles([['sales', 'Orders', num(t.orders), `${t.lines} order lines`], ['units', 'Units sold', num(t.units), t.orders ? `${(t.units / t.orders).toFixed(1)} units per order` : '–'],
               ['money', 'Revenue', money(t.revenue), 'qty × title price'], ['store', 'Stores selling', num(t.stores_selling), `of ${t.stores} stores`]])}
      <section class="card"><header><h2>Order lines</h2><span class="meta">joined to titles for the price</span></header>
        ${salesTable(d.rows, { full: true })}${pager(page, d.page_size, t.lines)}</section>`,
    mount(root) {
      const nav = (extra) => go(href('/sales', { stor_id: root.querySelector('#f-store').value, payterms: root.querySelector('#f-terms').value, ...extra }));
      root.querySelector('#f-store').addEventListener('change', () => nav({}));
      root.querySelector('#f-terms').addEventListener('change', () => nav({}));
      root.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => nav({ page: b.dataset.page })));
    },
  };
}

export async function newOrder({ lookups }) {
  const lk = await lookups();
  const today = new Date().toISOString().slice(0, 10);
  return {
    html: `${head('New Order', { back: '#/sales', crumb: ['Admin', 'New Order'], sub: 'Record an order line: one store, one title and a quantity.' })}
      <form class="card" novalidate>
        <div class="form-card"><div class="fields">
          ${field('stor_id', 'Store', `<select name="stor_id" required>${options(lk.stores.map((s) => [s.stor_id, `${s.stor_name} · ${s.city}`]), '', 'Select a store')}</select>`, { req: true })}
          ${field('ord_num', 'Order number', `<input name="ord_num" value="${esc(lk.next_order)}" maxlength="20" required>`, { req: true, hint: 'Next free number is filled in.' })}
          ${field('title_id', 'Title', `<select name="title_id" required>${options(lk.titles.map((t) => [t.title_id, `${t.title} (${t.title_id})`]), '', 'Select a title')}</select>`, { req: true, w: 6 })}
          ${field('qty', 'Quantity', '<input name="qty" type="number" min="1" max="32767" inputmode="numeric" required>', { req: true, w: 2 })}
          ${field('ord_date', 'Order date', `<input name="ord_date" type="date" value="${today}" required>`, { req: true, w: 2 })}
          ${field('payterms', 'Pay terms', `<select name="payterms" required>${options(lk.payterms.map((p) => [p, p]), 'Net 30')}</select>`, { req: true, w: 2 })}
          <div class="note info" style="grid-column:1/-1">${icon('info')}<div>An order can have several titles: save this line, then add the next title with the same store and order number.</div></div>
        </div>
        <aside class="panel" id="summary" aria-live="polite"></aside></div>
        <div class="result"></div>
        <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="#/sales">Cancel</a><button class="btn primary" type="submit">Save order</button></div>
      </form>`,
    mount(root) {
      const form = root.querySelector('form');
      const panel = root.querySelector('#summary');
      const draw = () => {
        const store = form.stor_id.value;
        const title = lk.titles.find((t) => t.title_id === form.title_id.value);
        const qty = Number(form.qty.value);
        if (!store || !title || !qty) {
          panel.innerHTML = '<h3>Order summary</h3><p>Pick a store, a title and a quantity to see the amount and the discounts that could apply.</p>';
          return;
        }
        const rules = lk.discounts.filter((d) => (d.stor_id === null || d.stor_id === store)
          && (d.lowqty === null || d.lowqty <= qty) && (d.highqty === null || qty <= d.highqty)).sort((a, b) => b.discount - a.discount);
        const name = (id) => (id ? lk.stores.find((s) => s.stor_id === id)?.stor_name : 'All stores');
        panel.innerHTML = `<h3>Order summary</h3>
          <div class="row"><span>Unit price</span><strong>${title.price === null ? 'Not priced' : money(title.price)}</strong></div>
          <div class="row"><span>Quantity</span><strong>${num(qty)}</strong></div>
          <div class="row"><span>Amount</span><span class="total">${title.price === null ? '–' : money(qty * title.price)}</span></div>
          <h3 style="margin-top:14px">Discounts that could apply</h3>
          ${rules.length ? rules.map((d) => `<div class="row"><div>${esc(d.discounttype)}<p style="margin:0">${esc(name(d.stor_id))}${d.lowqty !== null ? ` · Qty ${d.lowqty} – ${d.highqty}` : ''}</p></div><strong>${pct(d.discount)}</strong></div>`).join('') : '<p>None for this store and quantity.</p>'}
          <p style="color:var(--amber)">The sales table has no discount column, so the order itself stores no discount.</p>`;
      };
      form.addEventListener('input', draw);
      draw();
      onSubmit(form, (v) => api('/api/sales', { method: 'POST', body: v }),
        (out) => { form.querySelector('.result').innerHTML = savedNote(out, `Order ${out.ord_num}`, '#/sales', 'Back to Sales'); });
    },
  };
}

// ---------------------------------------------------------------- discounts and stores
export async function discounts() {
  const rows = await api('/api/discounts');
  const own = rows.filter((d) => d.stor_id);
  const best = rows.reduce((a, b) => (Number(b.discount) > Number(a.discount) ? b : a), rows[0]);
  return {
    html: `${head('Discounts', { count: rows.length, crumb: ['Admin', 'Discounts'], sub: 'Discount rules. A rule with no store applies to every store.' })}
      <div class="toolbar"><span class="spacer"></span><a class="btn primary" href="#/discounts/new">${icon('plus')} New discount</a></div>
      ${tiles([['discount', 'Discount rules', num(rows.length), plural(new Set(rows.map((d) => d.discounttype)).size, 'discount type')],
               ['store', 'Store-specific', num(own.length), `${rows.length - own.length} apply to all stores`],
               ['units', 'Quantity-based', num(rows.filter((d) => d.lowqty !== null).length), 'need a minimum order'],
               ['money', 'Highest discount', best ? pct(best.discount) : '–', best ? best.discounttype : '']])}
      <div class="grid-2">
        <section class="card"><header><h2>Discount rules</h2></header><div class="table-wrap"><table>
          <thead><tr><th>Discount type</th><th>Applies to</th><th>Quantity</th><th class="num">Discount</th></tr></thead><tbody>
          ${rows.map((d) => `<tr><td><strong>${esc(d.discounttype)}</strong></td><td>${d.stor_id ? `<strong style="color:var(--brand)">${esc(d.applies_to)}</strong>` : '<span class="muted">All stores</span>'}</td>
            <td class="muted">${d.lowqty === null ? 'Any quantity' : `${num(d.lowqty)} – ${num(d.highqty)}`}</td><td class="num"><strong style="color:var(--brand)">${pct(d.discount)}</strong></td></tr>`).join('')}
          </tbody></table></div></section>
        <section class="card"><header><h2>Stores with their own discount</h2></header><div class="body">
          ${own.map((d) => `<div class="list-row"><div class="ic">${icon('store')}</div><div class="text"><strong>${esc(d.applies_to)}</strong><small>${esc(d.city)}, ${esc(d.state)} · ID ${esc(d.stor_id)}</small></div><div class="end">${pct(d.discount)}</div></div>`).join('')}
          <div class="note info" style="margin-top:10px">${icon('info')}<div>Every other store only gets the all-store rules.</div></div></div></section>
      </div>`,
  };
}

export async function newDiscount({ lookups }) {
  const lk = await lookups();
  const types = [...new Set(lk.discounts.map((d) => d.discounttype))];
  return {
    html: `${head('New Discount', { back: '#/discounts', crumb: ['Admin', 'New Discount'], sub: 'Add a discount rule. Leave the store empty to apply it to every store.' })}
      <form class="card" novalidate><div class="form-card"><div class="fields">
        ${field('discounttype', 'Discount type', `<input name="discounttype" list="dtypes" maxlength="40" required placeholder="e.g. Customer Discount"><datalist id="dtypes">${types.map((t) => `<option value="${esc(t)}">`).join('')}</datalist>`, { req: true, w: 6 })}
        ${field('stor_id', 'Store', `<select name="stor_id">${options(lk.stores.map((s) => [s.stor_id, `${s.stor_name} · ${s.city}`]), '', 'All stores')}</select>`, { w: 6 })}
        ${field('lowqty', 'Low quantity', '<input name="lowqty" type="number" min="0" placeholder="Optional">', { w: 2 })}
        ${field('highqty', 'High quantity', '<input name="highqty" type="number" min="0" placeholder="Optional">', { w: 2 })}
        ${field('discount', 'Discount (%)', '<input name="discount" type="number" min="0" max="99.99" step="0.01" required placeholder="e.g. 4.5">', { req: true, w: 2 })}
      </div><aside class="panel" id="preview" aria-live="polite"></aside></div>
      <div class="result"></div>
      <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="#/discounts">Cancel</a><button class="btn primary" type="submit">Save discount</button></div></form>`,
    mount(root) {
      const form = root.querySelector('form');
      const panel = root.querySelector('#preview');
      const draw = () => {
        const v = Object.fromEntries(new FormData(form));
        const low = v.lowqty === '' ? null : Number(v.lowqty);
        const high = v.highqty === '' ? null : Number(v.highqty);
        const store = lk.stores.find((s) => s.stor_id === v.stor_id);
        const checks = [
          ['The discount is 0% or more', v.discount === '' || Number(v.discount) >= 0],
          ['The low quantity is 0 or more', low === null || low >= 0],
          ['The high quantity is at least the low one', low === null || high === null || high >= low],
        ];
        panel.innerHTML = `<h3>Preview</h3>
          <div class="card" style="padding:14px 16px;margin-bottom:14px"><div class="row" style="padding:0"><strong>${esc(v.discounttype || 'Discount type')}</strong><span class="total">${v.discount ? pct(v.discount) : '–'}</span></div>
          <p style="margin:4px 0 0">${store ? `${esc(store.stor_name)} only` : 'Every store'}</p>
          <p style="margin:2px 0 0">${low === null && high === null ? 'Any quantity' : `Orders of ${low ?? 0} – ${high ?? 'any'} copies`}</p></div>
          <h3>Database checks</h3>${checks.map(([l, ok]) => `<div class="row" style="justify-content:flex-start;gap:8px;color:${ok ? 'var(--brand)' : 'var(--red)'}">${icon(ok ? 'check' : 'info')}<span style="color:inherit">${l}</span></div>`).join('')}
          ${store ? `<p>${esc(store.stor_name)} ${lk.discounts.some((d) => d.stor_id === store.stor_id) ? 'already has' : 'has no'} discount of its own.</p>` : ''}`;
      };
      form.addEventListener('input', draw);
      draw();
      onSubmit(form, (v) => api('/api/discounts', { method: 'POST', body: v }),
        (out) => { form.querySelector('.result').innerHTML = savedNote(out, 'Discount', '#/discounts', 'Back to Discounts'); });
    },
  };
}

export async function stores() {
  const rows = await api('/api/stores');
  const top = rows.reduce((a, b) => (Number(b.revenue) > Number(a.revenue) ? b : a), rows[0]);
  return {
    html: `${head('Stores', { count: rows.length, crumb: ['Admin', 'Stores'], sub: 'Bookstores that place orders, with their sales and discounts.' })}
      ${tiles([['store', 'Stores', num(rows.length), `in ${new Set(rows.map((s) => s.state)).size} states`],
               ['sales', 'Orders', num(rows.reduce((n, s) => n + s.orders, 0)), 'placed by all stores'],
               ['money', 'Top store revenue', money(top?.revenue), top?.stor_name || ''],
               ['discount', 'Own discounts', num(rows.filter((s) => s.own_discount !== null).length), 'stores with their own rule']])}
      <section class="card"><div class="table-wrap"><table><thead><tr><th>ID</th><th>Store</th><th>Address</th><th>City</th><th class="num">Orders</th><th class="num">Units</th><th class="num">Revenue</th><th class="num">Discount</th></tr></thead><tbody>
        ${rows.map((s) => `<tr><td class="muted">${esc(s.stor_id)}</td><td><a href="#/sales?stor_id=${esc(s.stor_id)}"><strong>${esc(s.stor_name)}</strong></a></td><td class="muted">${esc(s.stor_address)}</td>
          <td>${esc(s.city)}, ${esc(s.state)} ${esc(s.zip)}</td><td class="num"><strong>${s.orders}</strong></td><td class="num">${num(s.units)}</td><td class="num"><strong>${money(s.revenue)}</strong></td>
          <td class="num">${s.own_discount === null ? '<span class="muted">–</span>' : `<strong style="color:var(--brand)">${pct(s.own_discount)}</strong>`}</td></tr>`).join('')}
      </tbody></table></div></section>`,
  };
}

// ---------------------------------------------------------------- publishers and employees
export async function publishers() {
  const rows = await api('/api/publishers');
  const priced = rows.filter((p) => p.avg_price !== null);
  return {
    html: `${head('Publishers', { count: rows.length, crumb: ['Admin', 'Publishers'], sub: 'Publishers, their titles and how many people work for each.' })}
      <div class="toolbar"><span class="spacer"></span><a class="btn primary" href="#/publishers/new">${icon('plus')} Add publisher</a></div>
      ${tiles([['publisher', 'Publishers', num(rows.length), plural(new Set(rows.map((p) => p.country)).size, 'country', 'countries')],
               ['library', 'Titles', num(rows.reduce((n, p) => n + p.titles, 0)), 'published'],
               ['employee', 'Employees', num(rows.reduce((n, p) => n + p.employees, 0)), 'across all publishers'],
               ['money', 'Average price', priced.length ? money(priced.reduce((n, p) => n + Number(p.avg_price) * p.titles, 0) / priced.reduce((n, p) => n + p.titles, 0)) : '–', 'per publisher title']])}
      <div class="pub-cards">${rows.map((p) => `<section class="card pub"><div class="head"><div class="logo">${initials(p.pub_name)}</div>
          <div><strong>${esc(p.pub_name)}</strong><small>${esc([p.city, p.state].filter(Boolean).join(', '))} · ${esc(p.country)} · ID ${esc(p.pub_id)}</small></div></div>
        <div class="pr"><b>PR INFO</b>${esc(p.pr_info || 'No PR info yet.')}</div>
        <div class="nums"><div><strong>${p.titles}</strong><span>Titles</span></div><div><strong>${p.employees}</strong><span>Employees</span></div></div>
        <div class="actions"><span class="muted">${p.avg_price === null ? 'No priced titles' : `Average price ${money(p.avg_price)}`}</span><a href="#/employees?pub_id=${esc(p.pub_id)}"><strong>View employees →</strong></a></div></section>`).join('')}</div>`,
  };
}

export async function newPublisher() {
  return {
    html: `${head('Add Publisher', { back: '#/publishers', crumb: ['Admin', 'Add Publisher'], sub: 'Add a publisher. The PR text is saved separately in pub_info.' })}
      <form class="card" novalidate><div class="form-card"><div class="fields">
        ${field('pub_id', 'Publisher ID', '<input name="pub_id" maxlength="4" inputmode="numeric" required placeholder="e.g. 9004">', { req: true, w: 2, hint: '4 digits' })}
        ${field('pub_name', 'Name', '<input name="pub_name" maxlength="40" required placeholder="e.g. Riverstone Books">', { req: true, w: 4 })}
        ${field('city', 'City', '<input name="city" maxlength="20" placeholder="e.g. Seattle">', { w: 3 })}
        ${field('state', 'State', '<input name="state" maxlength="2" placeholder="WA">', { w: 1 })}
        ${field('country', 'Country', '<input name="country" maxlength="30" value="USA">', { w: 2 })}
        ${field('pr_info', 'PR info', '<textarea name="pr_info" rows="4" placeholder="Short description shown on the publisher card…"></textarea>', { w: 6 })}
      </div><aside class="panel" id="preview"></aside></div>
      <div class="result"></div>
      <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="#/publishers">Cancel</a><button class="btn primary" type="submit">Save publisher</button></div></form>`,
    mount(root) {
      const form = root.querySelector('form');
      const panel = root.querySelector('#preview');
      const draw = () => {
        const v = Object.fromEntries(new FormData(form));
        const name = v.pub_name || 'New publisher';
        panel.innerHTML = `<h3>Card preview</h3><p>The new publisher appears like this on the Publishers tab.</p>
          <div class="card pub" style="margin-top:12px"><div class="head"><div class="logo">${initials(name)}</div><div><strong>${esc(name)}</strong>
          <small>${esc([v.city, v.state.toUpperCase()].filter(Boolean).join(', ') || 'City, state')} · ${esc(v.country || 'USA')} · ID ${esc(v.pub_id || '----')}</small></div></div>
          <div class="pr"><b>PR INFO</b>${esc(v.pr_info || 'No PR info yet.')}</div><div class="nums"><div><strong>0</strong><span>Titles</span></div><div><strong>0</strong><span>Employees</span></div></div></div>`;
      };
      form.addEventListener('input', draw);
      draw();
      onSubmit(form, (v) => api('/api/publishers', { method: 'POST', body: v }),
        (out) => { form.querySelector('.result').innerHTML = savedNote(out, 'Publisher', '#/publishers', 'Back to Publishers'); });
    },
  };
}

export async function employees({ query, go, lookups }) {
  const page = Number(query.page || 1);
  const [d, lk] = await Promise.all([api(`/api/employees${href('', { pub_id: query.pub_id, job_id: query.job_id, q: query.q, page, page_size: prefs().pageSize }).slice(1)}`), lookups()]);
  const t = d.totals;
  return {
    html: `${head('Employees', { count: t.employees, crumb: ['Admin', 'Employees'], sub: 'Everyone who works for a publisher, with their job and level.' })}
      <form class="toolbar" id="filters" role="search"><label class="sr-only" for="f-q">Search employees</label><input id="f-q" class="control" name="q" value="${esc(query.q || '')}" placeholder="Search name or ID">
        <label class="sr-only" for="f-pub">Publisher</label><select id="f-pub" class="control" name="pub_id">${options(lk.publishers.map((p) => [p.pub_id, p.pub_name]), query.pub_id, 'All publishers')}</select>
        <label class="sr-only" for="f-job">Job</label><select id="f-job" class="control" name="job_id">${options(lk.jobs.map((j) => [j.job_id, j.job_desc]), query.job_id, 'All jobs')}</select>
        <span class="spacer"></span><a class="btn primary" href="#/employees/new">${icon('plus')} Add employee</a></form>
      ${tiles([['employee', 'Employees', num(t.employees), `at ${plural(t.publishers, 'publisher')}`], ['idcard', 'Jobs in use', num(t.jobs_in_use), `of ${t.jobs} jobs defined`],
               ['cal', 'Newest hire', t.newest_hire ? date(t.newest_hire, true) : '–', 'by hire date'], ['info', 'Level out of range', num(t.out_of_range), 'job level outside the job’s min–max', t.out_of_range > 0]])}
      <section class="card"><header><h2>Employees</h2><span class="meta">sorted by last name · red levels are outside the job’s range</span></header>
        ${d.rows.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Emp ID</th><th>Publisher</th><th>Job</th><th>Level (job range)</th><th class="num">Hired</th></tr></thead><tbody>
        ${d.rows.map((e) => `<tr><td><div class="who"><span class="mini-avatar">${initials(`${e.fname} ${e.lname}`)}</span><strong>${esc(e.fname)} ${esc(e.lname)}</strong></div></td>
          <td class="muted">${esc(e.emp_id)}</td><td>${esc(e.pub_name)}</td><td>${esc(e.job_desc)}</td><td>${level(e)}</td><td class="num muted">${date(e.hire_date, true)}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">No employees match these filters.</div>'}${pager(page, d.page_size, t.employees)}</section>`,
    mount(root) {
      const form = root.querySelector('#filters');
      const nav = (extra) => go(href('/employees', { ...Object.fromEntries(new FormData(form)), ...extra }));
      form.addEventListener('submit', (ev) => { ev.preventDefault(); nav({}); });
      form.querySelectorAll('select').forEach((s) => s.addEventListener('change', () => nav({})));
      root.querySelectorAll('[data-page]').forEach((b) => b.addEventListener('click', () => nav({ page: b.dataset.page })));
    },
  };
}

export async function newEmployee({ lookups }) {
  const lk = await lookups();
  return {
    html: `${head('Add Employee', { back: '#/employees', crumb: ['Admin', 'Add Employee'], sub: 'Add someone who works for a publisher.' })}
      <form class="card" novalidate><div class="form-card"><div class="fields">
        ${field('emp_id', 'Employee ID', '<input name="emp_id" maxlength="9" required placeholder="e.g. KRR41027F" autocapitalize="characters">', { req: true, hint: 'Initials, 5 digits, then M or F' })}
        ${field('hire_date', 'Hire date', '<input name="hire_date" type="date">', { hint: 'Empty means today' })}
        ${field('fname', 'First name', '<input name="fname" maxlength="20" required>', { req: true, w: 2 })}
        ${field('minit', 'M.I.', '<input name="minit" maxlength="1">', { w: 1 })}
        ${field('lname', 'Last name', '<input name="lname" maxlength="30" required>', { req: true, w: 3 })}
        ${field('pub_id', 'Publisher', `<select name="pub_id" required>${options(lk.publishers.map((p) => [p.pub_id, p.pub_name]), '', 'Select a publisher')}</select>`, { req: true })}
        ${field('job_id', 'Job', `<select name="job_id" required>${options(lk.jobs.map((j) => [j.job_id, `${j.job_desc} (${j.min_lvl}–${j.max_lvl})`]), '', 'Select a job')}</select>`, { req: true })}
        ${field('job_lvl', 'Job level', '<input name="job_lvl" type="number" min="0" required>', { req: true, w: 2 })}
      </div><aside class="panel" id="check" aria-live="polite"></aside></div>
      <div class="result"></div>
      <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="#/employees">Cancel</a><button class="btn primary" type="submit">Save employee</button></div></form>`,
    mount(root) {
      const form = root.querySelector('form');
      const panel = root.querySelector('#check');
      const draw = () => {
        const job = lk.jobs.find((j) => String(j.job_id) === form.job_id.value);
        const lvl = form.job_lvl.value === '' ? null : Number(form.job_lvl.value);
        let check = '<p>Pick a job and a level to check that the level fits the job’s range.</p>';
        if (job && lvl !== null) {
          const ok = lvl >= job.min_lvl && lvl <= job.max_lvl;
          check = `<div class="note ${ok ? 'ok' : 'bad'}">${icon(ok ? 'check' : 'info')}<div><strong>${lvl} is ${ok ? 'inside' : 'outside'} the range</strong><br>${esc(job.job_desc)}: ${job.min_lvl} – ${job.max_lvl}</div></div>
            ${level({ job_lvl: lvl, min_lvl: job.min_lvl, max_lvl: job.max_lvl, level_in_range: ok })}`;
        } else if (job) {
          check = `<p>${esc(job.job_desc)} allows levels ${job.min_lvl} – ${job.max_lvl}.</p>`;
        }
        panel.innerHTML = `<h3>Level check</h3>${check}<p style="margin-top:14px">The database itself does not check the level against the job, so this form does. Employee IDs must look like KRR41027F.</p>`;
      };
      form.addEventListener('input', draw);
      draw();
      onSubmit(form, (v) => api('/api/employees', { method: 'POST', body: v }),
        (out) => { form.querySelector('.result').innerHTML = savedNote(out, `Employee ${out.emp_id}`, '#/employees', 'Back to Employees'); });
    },
  };
}

// ---------------------------------------------------------------- author access
export async function authorAccess({ reload }) {
  const rows = await api('/api/authors');
  const withLogin = rows.filter((a) => a.username);
  const loginCell = (a) => {
    if (!a.username) return `<button class="btn small" data-create="${esc(a.au_id)}" data-name="${esc(a.name)}">${icon('plus')} Create</button>`;
    return `<span class="pill ${a.is_active ? 'dot' : 'red'}">${esc(a.username)}${a.is_active ? '' : ' · disabled'}</span>`;
  };
  const actions = (a) => (a.username ? `<span class="actions">
      <button class="btn small" data-password="${esc(a.username)}" data-name="${esc(a.name)}">${icon('key')} ${a.has_password ? 'Reset password' : 'Set password'}</button>
      <button class="btn small${a.is_active ? ' danger' : ''}" data-active="${esc(a.username)}" data-to="${a.is_active ? '0' : '1'}">${a.is_active ? 'Disable' : 'Enable'}</button></span>` : '');
  return {
    html: `${head('Author Access', { count: rows.length, crumb: ['Admin', 'Author Access'], sub: 'Only admins create logins. An author with a login can add and delete their own books.' })}
      <div class="toolbar"><span class="spacer"></span><a class="btn primary" href="#/authors/new">${icon('plus')} Add author</a></div>
      ${tiles([['community', 'Authors', num(rows.length), 'in the authors table'], ['edit', 'With contract', num(rows.filter((a) => a.contract).length), 'contract = true'],
               ['access', 'Active logins', num(withLogin.filter((a) => a.is_active).length), withLogin.filter((a) => a.is_active).map((a) => a.name).join(', ') || 'none'],
               ['library', 'Books managed', num(withLogin.reduce((n, a) => n + a.books, 0)), 'by authors with a login']])}
      <section class="card"><div class="table-wrap"><table><thead><tr><th>Author</th><th>Author ID</th><th>City</th><th>Contract</th><th class="num">Books</th><th>Login</th><th>Password</th><th>Last login</th><th></th></tr></thead><tbody>
        ${rows.map((a) => `<tr${a.username ? ' class="highlight"' : ''}><td><div class="who"><span class="mini-avatar">${initials(a.name)}</span><strong>${esc(a.name)}</strong></div></td>
          <td class="muted">${esc(a.au_id)}</td><td>${esc(a.city)}</td><td>${a.contract ? '<span class="pill">Yes</span>' : '<span class="pill red">No</span>'}</td><td class="num"><strong>${a.books}</strong></td>
          <td>${loginCell(a)}</td>
          <td>${a.username ? (a.has_password ? 'Set' : '<span class="pill amber">Not set yet</span>') : '<span class="muted">–</span>'}</td>
          <td class="muted">${a.username ? (a.last_login ? date(a.last_login) : 'Never') : '–'}</td><td>${actions(a)}</td></tr>`).join('')}
      </tbody></table></div></section>
      <dialog id="login-dialog"><form method="dialog" novalidate>
        <div class="dbody"><div class="dicon ok">${icon('key')}</div><div><h2 id="login-title"></h2><p id="login-sub"></p>
          <div class="dialog-fields">
            ${field('username', 'Username', '<input name="username" autocomplete="off" autocapitalize="none" spellcheck="false" maxlength="40">', { req: true, hint: '3–40 characters: lowercase letters, digits, dot, dash or underscore.' })}
            ${field('password', 'Password', '<input name="password" type="password" autocomplete="new-password">', { req: true, hint: 'At least 5 characters. Share it with the author privately.' })}
            ${field('confirm', 'Type it again', '<input name="confirm" type="password" autocomplete="new-password">', { req: true })}
          </div><div class="result"></div></div></div>
        <footer><button class="btn" type="button" id="login-cancel">Cancel</button><button class="btn primary" type="submit" id="login-ok"></button></footer>
      </form></dialog>`,
    mount(root) {
      const dlg = root.querySelector('#login-dialog');
      const form = dlg.querySelector('form');
      let send = null;
      const open = (title, sub, button, withUsername, suggested, action) => {
        form.reset();
        showErrors(form, {});
        form.querySelector('.result').innerHTML = '';
        root.querySelector('#login-title').textContent = title;
        root.querySelector('#login-sub').textContent = sub;
        root.querySelector('#login-ok').textContent = button;
        form.querySelector('[data-field="username"]').hidden = !withUsername;
        form.username.value = suggested;
        send = action;
        dlg.showModal();
        (withUsername ? form.username : form.password).focus();
      };
      const suggest = (name) => { const p = name.toLowerCase().replace(/[^a-z ]/g, '').split(' ').filter(Boolean); return p.length > 1 ? p[0][0] + p[p.length - 1] : p[0] || ''; };
      root.querySelectorAll('[data-create]').forEach((b) => b.addEventListener('click', () => open(
        `Create a login for ${b.dataset.name}`, 'They can sign in straight away and manage only their own books.', 'Create login', true, suggest(b.dataset.name),
        (v) => api('/api/logins', { method: 'POST', body: { au_id: b.dataset.create, username: v.username, password: v.password } })
          .then(() => `Login ${v.username} created for ${b.dataset.name}.`))));
      root.querySelectorAll('[data-password]').forEach((b) => b.addEventListener('click', () => open(
        `New password for ${b.dataset.name}`, `@${b.dataset.password} will be signed out everywhere and must use the new password.`, 'Set password', false, '',
        (v) => api(`/api/logins/${encodeURIComponent(b.dataset.password)}/password`, { method: 'PUT', body: { password: v.password } })
          .then(() => `Password set for ${b.dataset.password}.`))));
      root.querySelector('#login-cancel').addEventListener('click', () => dlg.close());
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const button = root.querySelector('#login-ok');
        const v = { username: form.username.value.trim().toLowerCase(), password: form.password.value, confirm: form.confirm.value };
        if (v.password !== v.confirm) { showErrors(form, { confirm: 'The two passwords are different.' }); return; }
        button.disabled = true;
        try {
          const message = await send(v);
          dlg.close();
          toast(message);
          reload();
        } catch (err) {
          showErrors(form, err.errors);
          form.querySelector('.result').innerHTML = Object.keys(err.errors || {}).length ? '' : `<div class="note bad">${icon('info')}<div>${esc(err.message)}</div></div>`;
        } finally {
          button.disabled = false;
        }
      });
      root.querySelectorAll('[data-active]').forEach((b) => b.addEventListener('click', async () => {
        const on = b.dataset.to === '1';
        if (!on && !window.confirm(`Disable ${b.dataset.active}? They are signed out at once and can't sign in until you enable the login again.`)) return;
        try {
          await api(`/api/logins/${encodeURIComponent(b.dataset.active)}/active`, { method: 'PUT', body: { is_active: on } });
          toast(`${b.dataset.active} ${on ? 'enabled' : 'disabled'}.`);
          reload();
        } catch (err) { toast(err.message, true); }
      }));
    },
  };
}

// ---------------------------------------------------------------- add a new author (authors row) and, optionally, their login
export async function newAuthor({ user }) {
  return {
    html: `${head('Add Author', { back: '#/authors', crumb: ['Author Access', 'Add Author'], sub: 'Someone new who is not in the authors table yet. Their details go into authors; the login lets them sign in.' })}
      <form class="card" novalidate><div class="form-card"><div class="fields">
        <h3 class="f w6 section">Author details</h3>
        ${field('au_fname', 'First name', '<input name="au_fname" maxlength="20" required placeholder="e.g. Priya">', { req: true, w: 3 })}
        ${field('au_lname', 'Last name', '<input name="au_lname" maxlength="40" required placeholder="e.g. Sharma">', { req: true, w: 3 })}
        ${field('phone', 'Phone', '<input name="phone" maxlength="12" placeholder="e.g. 415 555-0101">', { w: 3, hint: 'Saved as UNKNOWN if left blank.' })}
        ${field('contract', 'Contract signed', `<select name="contract">${options([['true', 'Yes'], ['false', 'No']], 'true')}</select>`, { req: true, w: 3 })}
        ${field('address', 'Address', '<input name="address" maxlength="40" placeholder="e.g. 12 Market St.">', { w: 6 })}
        ${field('city', 'City', '<input name="city" maxlength="20" placeholder="e.g. Oakland">', { w: 2 })}
        ${field('state', 'State', '<input name="state" maxlength="2" placeholder="CA">', { w: 2 })}
        ${field('zip', 'ZIP', '<input name="zip" maxlength="5" inputmode="numeric" placeholder="94612">', { w: 2, hint: '5 digits' })}
        ${field('au_id', 'Author ID', '<input name="au_id" maxlength="11" placeholder="Leave blank to create one">', { w: 3, hint: 'Format 999-99-9999. Blank: a free ID is picked for you.' })}
        <h3 class="f w6 section">Login</h3>
        <label class="f w6 check-row"><input type="checkbox" name="with_login" checked> Create a login so they can sign in now</label>
        <div class="f w6 login-fields" style="display:contents">
          ${field('username', 'Username', '<input name="username" maxlength="40" autocomplete="off" autocapitalize="none" spellcheck="false">', { req: true, w: 2, hint: 'Lowercase, 3+ characters' })}
          ${field('password', 'Password', '<input name="password" type="password" autocomplete="new-password">', { req: true, w: 2, hint: 'At least 5 characters.' })}
          ${field('confirm', 'Type it again', '<input name="confirm" type="password" autocomplete="new-password">', { req: true, w: 2 })}
        </div>
      </div><aside class="panel" id="preview"></aside></div>
      <div class="result"></div>
      <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="#/authors">Cancel</a><button class="btn primary" type="submit">Save author</button></div></form>`,
    mount(root) {
      const form = root.querySelector('form');
      const panel = root.querySelector('#preview');
      let typedUsername = false;
      const suggest = () => {
        const f = (form.au_fname.value.trim()[0] || '').toLowerCase();
        const l = form.au_lname.value.trim().toLowerCase().replace(/[^a-z0-9]/g, '');
        return `${f}${l}`.slice(0, 40);
      };
      const loginOn = () => form.with_login.checked;
      const draw = () => {
        if (!typedUsername) form.username.value = suggest();
        root.querySelectorAll('.login-fields .f').forEach((f) => { f.hidden = !loginOn(); });
        const name = `${form.au_fname.value.trim()} ${form.au_lname.value.trim()}`.trim() || 'New author';
        const place = [form.city.value.trim(), form.state.value.trim().toUpperCase()].filter(Boolean).join(', ');
        panel.innerHTML = `<h3>What gets saved</h3>
          <div class="list-row"><div class="ic"><span class="mini-avatar" style="margin:0">${esc(initials(name))}</span></div>
            <div class="text"><strong>${esc(name)}</strong><small>${esc(place || 'No city yet')} · ${form.contract.value === 'true' ? 'Contract signed' : 'No contract'}</small></div></div>
          <p><strong>authors</strong>: one new row${form.au_id.value.trim() ? `, ID ${esc(form.au_id.value.trim())}` : ' with a new ID'}.</p>
          <p>${loginOn() ? `<strong>app_users</strong>: login <strong>@${esc(form.username.value.trim().toLowerCase() || '…')}</strong>, role author, created by ${esc(user.username)}. They can sign in straight away.`
            : 'No login yet. You can create one later from Author Access.'}</p>
          <p class="muted">Both are saved together: if either is rejected, neither is stored.</p>`;
      };
      form.username.addEventListener('input', () => { typedUsername = form.username.value.trim() !== ''; });
      form.addEventListener('input', draw);
      form.addEventListener('change', draw);
      draw();
      onSubmit(form, (v) => {
        const login = loginOn() ? { username: (v.username || '').toLowerCase(), password: form.password.value } : null;
        if (login && form.password.value !== form.confirm.value) {
          return Promise.reject(new ApiError(422, { detail: 'Please fix the highlighted fields.', errors: { confirm: 'The two passwords are different.' } }));
        }
        const author = { au_id: v.au_id, au_fname: v.au_fname, au_lname: v.au_lname, phone: v.phone, address: v.address,
          city: v.city, state: v.state, zip: v.zip, contract: v.contract === 'true' };
        return api('/api/authors', { method: 'POST', body: { author, login } });
      }, (out) => {
        const result = form.querySelector('.result');
        result.innerHTML = out.saved
          ? `<div class="note ok">${icon('check')}<div><strong>${esc(out.name)} added</strong> with author ID ${esc(out.au_id)}.
              ${out.username ? ` They can sign in now as <strong>@${esc(out.username)}</strong>.` : ''}<br><a href="#/authors">Back to Author Access</a></div></div>`
          : savedNote(out, `Author ${out.name}`, '#/authors', 'Back to Author Access');
        if (out.saved) form.querySelector('button[type="submit"]').style.display = 'none';   // a second click would add the same person twice
      });
    },
  };
}
