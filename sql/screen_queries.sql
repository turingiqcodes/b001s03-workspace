-- The query behind each Digital Library screen (svg/).
--
-- The SELECTs are safe to run as they are; parameters default to the demo values:
--   psql -X -f sql/screen_queries.sql
--   psql -X -v au_id="'172-32-1176'" -v title_id="'BU1032'" -f sql/screen_queries.sql
--
-- The writes at the end are templates for the app (commented out). They are the only
-- statements that change the pubs tables, and only when an admin or author saves a form.

\if :{?au_id} \else \set au_id '486-29-1786' \endif
\if :{?title_id} \else \set title_id 'MC3021' \endif
\if :{?pub_id} \else \set pub_id '9002' \endif

-- Shared: titles an author removed stay in titles but are hidden everywhere in the app.
CREATE TEMP VIEW visible_titles AS
SELECT t.*
FROM public.titles t
WHERE NOT EXISTS (SELECT 1 FROM public.app_title_removals r WHERE r.title_id = t.title_id);


-- ============================================================ admin

-- Admin Dashboard: the four totals
SELECT (SELECT count(DISTINCT (stor_id, ord_num)) FROM public.sales)                    AS orders,
       (SELECT count(*) FROM public.sales)                                              AS order_lines,
       (SELECT sum(s.qty * t.price) FROM public.sales s JOIN public.titles t USING (title_id)) AS revenue,
       (SELECT sum(qty) FROM public.sales)                                              AS units,
       (SELECT count(*) FROM public.publishers)                                         AS publishers,
       (SELECT count(*) FROM public.employee)                                           AS employees;

-- Library: every visible title with its authors in order
SELECT t.title_id, t.title, t.type, t.price, t.ytd_sales,
       string_agg(a.au_fname || ' ' || a.au_lname, ', ' ORDER BY ta.au_ord) AS authors
FROM visible_titles t
LEFT JOIN public.titleauthor ta USING (title_id)
LEFT JOIN public.authors a USING (au_id)
GROUP BY t.title_id, t.title, t.type, t.price, t.ytd_sales
ORDER BY t.ytd_sales DESC NULLS LAST;

-- Title detail (titles/<id>.svg): the book, its authors, royalty tiers and sales
SELECT t.*, p.pub_name FROM public.titles t LEFT JOIN public.publishers p USING (pub_id) WHERE t.title_id = :'title_id';

SELECT a.au_fname || ' ' || a.au_lname AS author, a.au_id, ta.au_ord, ta.royaltyper,
       sum(ta.royaltyper) OVER () AS total_share          -- should be 100; the screen warns when it is not
FROM public.titleauthor ta JOIN public.authors a USING (au_id)
WHERE ta.title_id = :'title_id'
ORDER BY ta.au_ord;

SELECT lorange, hirange, royalty FROM public.roysched WHERE title_id = :'title_id' ORDER BY lorange;

SELECT s.ord_num, s.ord_date, st.stor_name, s.qty, s.qty * t.price AS amount
FROM public.sales s
JOIN public.stores st USING (stor_id)
JOIN public.titles t USING (title_id)
WHERE s.title_id = :'title_id'
ORDER BY s.ord_date DESC;

-- Removed books
SELECT r.title_id, t.title, u.username AS removed_by, r.removed_at
FROM public.app_title_removals r
JOIN public.titles t USING (title_id)
JOIN public.app_users u ON u.user_id = r.removed_by
ORDER BY r.removed_at DESC;

-- Sales: order lines with price and amount, newest first
SELECT s.ord_num, s.ord_date, st.stor_name, t.title, s.qty, t.price, s.qty * t.price AS amount, s.payterms
FROM public.sales s
JOIN public.stores st USING (stor_id)
JOIN public.titles t USING (title_id)
ORDER BY s.ord_date DESC, s.ord_num DESC;

-- Discounts: every rule; no store means it applies to all stores
SELECT d.discounttype, coalesce(st.stor_name, 'All stores') AS applies_to, d.lowqty, d.highqty, d.discount
FROM public.discounts d
LEFT JOIN public.stores st USING (stor_id)
ORDER BY d.discounttype, d.discount DESC;

-- Stores: each store with its orders, units, revenue and own discount
SELECT st.stor_id, st.stor_name, st.stor_address, st.city, st.state, st.zip,
       count(DISTINCT s.ord_num)  AS orders,
       coalesce(sum(s.qty), 0)    AS units,
       coalesce(sum(s.qty * t.price), 0) AS revenue,
       (SELECT d.discount FROM public.discounts d WHERE d.stor_id = st.stor_id ORDER BY d.discount DESC LIMIT 1) AS own_discount
FROM public.stores st
LEFT JOIN public.sales s USING (stor_id)
LEFT JOIN public.titles t USING (title_id)
GROUP BY st.stor_id
ORDER BY st.stor_id;

-- Publishers: cards with PR info, title and employee counts
SELECT p.pub_id, p.pub_name, p.city, p.state, p.country, pi.pr_info,
       (SELECT count(*) FROM public.titles t WHERE t.pub_id = p.pub_id)   AS titles,
       (SELECT count(*) FROM public.employee e WHERE e.pub_id = p.pub_id) AS employees
FROM public.publishers p
LEFT JOIN public.pub_info pi USING (pub_id)
ORDER BY p.pub_id;

-- Employees: job, level and whether the level is inside the job's range
SELECT e.emp_id, e.fname, e.minit, e.lname, p.pub_name, j.job_desc, e.job_lvl, j.min_lvl, j.max_lvl,
       e.job_lvl BETWEEN j.min_lvl AND j.max_lvl AS level_in_range, e.hire_date
FROM public.employee e
JOIN public.jobs j USING (job_id)
JOIN public.publishers p USING (pub_id)
ORDER BY e.lname, e.fname;

-- Author Access: every author, their number of books and login status
SELECT a.au_id, a.au_fname || ' ' || a.au_lname AS author, a.city, a.contract,
       (SELECT count(*) FROM public.titleauthor ta WHERE ta.au_id = a.au_id) AS books,
       u.username, u.is_active
FROM public.authors a
LEFT JOIN public.app_users u ON u.au_id = a.au_id
ORDER BY u.user_id IS NULL, a.au_lname, a.au_fname;


-- ============================================================ author (:au_id is the logged-in author)

-- My Books
SELECT t.title_id, t.title, t.type, p.pub_name, t.price, t.pubdate, t.ytd_sales, t.notes, ta.au_ord, ta.royaltyper,
       (SELECT count(*) FROM public.titleauthor x WHERE x.title_id = t.title_id) AS author_count
FROM public.titleauthor ta
JOIN visible_titles t USING (title_id)
LEFT JOIN public.publishers p USING (pub_id)
WHERE ta.au_id = :'au_id'
ORDER BY t.pubdate DESC NULLS LAST;

-- My Profile
SELECT a.*, u.username, u.created_at, u.last_login
FROM public.authors a
LEFT JOIN public.app_users u USING (au_id)
WHERE a.au_id = :'au_id';


-- ============================================================ writes (templates for the app, not run here)
-- Each runs in one transaction. :params come from the form.
--
-- New Order (one order line; the sales key is store + order number + title)
--   INSERT INTO public.sales (stor_id, ord_num, ord_date, qty, payterms, title_id)
--   VALUES (:stor_id, :ord_num, :ord_date, :qty, :payterms, :title_id);
--
-- New Discount (stor_id NULL = every store)
--   INSERT INTO public.discounts (discounttype, stor_id, lowqty, highqty, discount)
--   VALUES (:discounttype, :stor_id, :lowqty, :highqty, :discount);
--
-- Add Publisher (PR text goes to pub_info)
--   INSERT INTO public.publishers (pub_id, pub_name, city, state, country) VALUES (:pub_id, :pub_name, :city, :state, :country);
--   INSERT INTO public.pub_info (pub_id, pr_info) VALUES (:pub_id, :pr_info);
--
-- Add Employee (the database does not check the level against the job, so the app must)
--   INSERT INTO public.employee (emp_id, fname, minit, lname, job_id, job_lvl, pub_id, hire_date)
--   SELECT :emp_id, :fname, :minit, :lname, j.job_id, :job_lvl, :pub_id, :hire_date
--   FROM public.jobs j
--   WHERE j.job_id = :job_id AND :job_lvl BETWEEN j.min_lvl AND j.max_lvl;   -- 0 rows inserted = level out of range
--
-- Add Book (admin picks :au_id; for an author it is their own au_id)
--   INSERT INTO public.titles (title_id, title, type, pub_id, price, advance, royalty, notes, pubdate)
--   VALUES (:title_id, :title, :type, :pub_id, :price, :advance, :royalty, :notes, :pubdate);
--   INSERT INTO public.titleauthor (au_id, title_id, au_ord, royaltyper) VALUES (:au_id, :title_id, 1, 100);
--
-- Edit Book (an author may only edit a title they are on)
--   UPDATE public.titles t SET title = :title, type = :type, pub_id = :pub_id, price = :price,
--          advance = :advance, royalty = :royalty, notes = :notes, pubdate = :pubdate
--   WHERE t.title_id = :title_id
--     AND EXISTS (SELECT 1 FROM public.titleauthor ta WHERE ta.title_id = t.title_id AND ta.au_id = :au_id);
--
-- Delete Book (author) / Restore (admin): see sql/app_users.sql
