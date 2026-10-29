// Add / edit a book. Admins pick the author; an author is always linked to themself.
import { icon } from './icons.js';
import { api, cover, esc, field, head, onSubmit, options, savedNote } from './lib.js';

export async function bookForm({ params, user, lookups }) {
  const admin = user.role === 'admin';
  const editing = Boolean(params.id);
  const [lk, current] = await Promise.all([lookups(), editing ? api(`/api/titles/${encodeURIComponent(params.id)}`) : null]);
  const t = current?.title || {};
  const list = admin ? (editing ? `#/titles/${encodeURIComponent(params.id)}` : '#/library') : '#/my-books';
  const listLabel = admin ? (editing ? 'Back to the book' : 'Back to Library') : 'Back to My Books';
  const authorName = editing ? current.authors[0]?.name : (admin ? '' : user.name);
  const title = editing ? 'Edit Book' : 'Add New Book';
  const sub = editing ? `Change the details of “${t.title}”. The title ID can’t be changed.`
    : admin ? 'Fill in the details below and pick the book’s author.' : 'Fill in the details below. The book will be linked to you as its author.';
  const authorField = editing
    ? field('authors', 'Authors', `<div class="locked">${esc(current.authors.map((a) => a.name).join(', ') || 'No author linked')}${icon('lock')}</div>`, { w: 6, hint: 'Authors are managed in titleauthor.' })
    : admin
      ? field('au_id', 'Author', `<select name="au_id" required>${options(lk.authors.map((a) => [a.au_id, a.name]), '', 'Select an author')}</select>`, { req: true, hint: 'Linked as author 1 with a 100% royalty share.' })
      : field('au_id', 'Author', `<div class="locked"><span class="mini-avatar" style="background:var(--brand);color:var(--on-brand)">${esc(user.name.split(' ').map((p) => p[0]).join(''))}</span>${esc(user.name)} (you)${icon('lock')}</div>`, { hint: 'Added automatically, order 1 with a 100% royalty share.' });
  return {
    html: `${head(title, { back: list, crumb: [admin ? (editing ? 'Library' : 'Admin') : user.name, title], sub })}
      <form class="card" novalidate><div class="form-card with-cover">
        <div><div class="cover-box" id="cover"></div><p class="sub" style="text-align:center;margin-top:8px">The cover is made from the title and type.</p>
          <div class="panel" style="margin-top:12px"><h3>Where this is saved</h3><p>Book details → titles</p><p>${editing ? 'Authors stay as they are' : 'Author link → titleauthor'}</p></div></div>
        <div class="fields">
          ${field('title', 'Book title', `<input name="title" maxlength="80" required value="${esc(t.title || '')}" placeholder="e.g. Practical Data Pipelines">`, { req: true, w: 6 })}
          ${field('title_id', 'Title ID', editing ? `<div class="locked">${esc(t.title_id)}${icon('lock')}</div>` : '<input name="title_id" maxlength="6" required placeholder="e.g. PC1001" autocapitalize="characters">', { req: !editing, hint: editing ? '' : '2 letters and 4 digits' })}
          ${field('type', 'Type', `<select name="type" required>${options(lk.types.map((x) => [x.value, x.label]), String(t.type || '').trim(), editing ? undefined : 'Select a type')}</select>`, { req: true })}
          ${field('pub_id', 'Publisher', `<select name="pub_id" required>${options(lk.publishers.map((p) => [p.pub_id, p.pub_name]), t.pub_id, 'Select a publisher')}</select>`, { req: true })}
          ${field('pubdate', 'Publication date', `<input name="pubdate" type="date" value="${esc(t.pubdate || '')}">`, { hint: editing ? '' : 'Empty means today' })}
          ${field('price', 'Price ($)', `<input name="price" type="number" min="0" step="0.01" value="${t.price ?? ''}" placeholder="${editing && t.price === null ? 'Not set yet' : 'e.g. 19.99'}">`, { w: 2 })}
          ${field('advance', 'Advance ($)', `<input name="advance" type="number" min="0" step="0.01" value="${t.advance ?? ''}" placeholder="${editing && t.advance === null ? 'Not set yet' : 'e.g. 5000'}">`, { w: 2 })}
          ${field('royalty', 'Royalty (%)', `<input name="royalty" type="number" min="0" max="100" value="${t.royalty ?? ''}" placeholder="${editing && t.royalty === null ? 'Not set yet' : 'e.g. 10'}">`, { w: 2 })}
          ${field('notes', 'Notes', `<textarea name="notes" maxlength="200" placeholder="A short summary of the book…">${esc(t.notes || '')}</textarea>`, { w: 6 })}
          ${authorField}
        </div></div>
        <div class="result"></div>
        <div class="form-foot">Fields marked * are required.<span class="spacer"></span><a class="btn" href="${list}">Cancel</a><button class="btn primary" type="submit">${editing ? 'Save changes' : 'Save Book'}</button></div>
      </form>`,
    mount(root) {
      const form = root.querySelector('form');
      const box = root.querySelector('#cover');
      const draw = () => {
        const who = admin && !editing ? lk.authors.find((a) => a.au_id === form.au_id.value)?.name : authorName;
        box.innerHTML = cover({ title: form.title.value || 'Your book title', type: form.type.value }, who);
      };
      form.addEventListener('input', draw);
      draw();
      onSubmit(form, (v) => (editing
        ? api(`/api/titles/${encodeURIComponent(params.id)}`, { method: 'PUT', body: v })
        : api('/api/titles', { method: 'POST', body: { ...v, title_id: v.title_id?.toUpperCase() ?? null } })),
      (out) => { form.querySelector('.result').innerHTML = savedNote(out, editing ? 'Changes' : `Book ${out.title_id}`, list, listLabel); });
    },
  };
}
